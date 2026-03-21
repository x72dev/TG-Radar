from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient, events, functions, types, utils

from .command_bus import CommandBus
from .compat import seed_db_from_legacy_config_if_needed
from .config import load_config, sync_snapshot_to_config, update_config_data
from .core.plugin_system import PluginManager
from .db import AdminJob, RadarDB, RouteTask
from .executors import JobResult
from .logger import setup_logger
from .scheduler import AdminScheduler
from .sync_logic import RouteReport, SyncReport, scan_auto_routes, sync_dialog_folders
from .telegram_utils import (
    blockquote_preview,
    bullet,
    dialog_filter_title,
    escape,
    format_duration,
    html_code,
    merge_patterns,
    normalize_pattern_from_terms,
    panel,
    section,
    shorten_path,
    soft_kv,
    split_terms,
    try_remove_terms_from_pattern,
)
from .version import __version__


class AdminApp:
    def __init__(self, work_dir: Path) -> None:
        self.config = load_config(work_dir)
        self.logger = setup_logger("tr-manager-admin", self.config.logs_dir / "admin.log")
        self.db = RadarDB(self.config.db_path)
        seed_db_from_legacy_config_if_needed(work_dir, self.db)
        self.started_at = datetime.now()
        self.stop_event = asyncio.Event()
        self.command_client: TelegramClient | None = None
        self.worker_client: TelegramClient | None = None
        self.client: TelegramClient | None = None
        self.bg_tasks: set[asyncio.Task] = set()
        self.last_command_ts = 0.0
        self.command_bus = CommandBus(self.db, notifier=self._notify_scheduler)
        self.scheduler: AdminScheduler | None = None
        self.last_sync_result: tuple[SyncReport, RouteReport] | None = None
        self.self_id: int | None = None
        self._last_snapshot_queued_at = 0.0
        self._startup_sync_note = ""
        self.plugin_manager = PluginManager(self)

    def _notify_scheduler(self) -> None:
        if self.scheduler is not None:
            self.scheduler.notify_new_job()

    async def run(self) -> None:
        self.config.sessions_dir.mkdir(parents=True, exist_ok=True)
        admin_session = self.config.admin_session.with_suffix(".session")
        worker_session = self.config.admin_worker_session.with_suffix(".session")
        if not admin_session.exists():
            raise FileNotFoundError("Missing runtime/sessions/tg_radar_admin.session. Run bootstrap_session.py first.")
        if not worker_session.exists():
            try:
                import shutil
                shutil.copy2(admin_session, worker_session)
            except Exception as exc:
                raise FileNotFoundError("Missing runtime/sessions/tg_radar_admin_worker.session.") from exc

        lock_file = self.config.work_dir / ".admin.lock"
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
        try:
            if os.name != "nt":
                import fcntl
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception as exc:
            raise RuntimeError("tg-radar-admin is already running") from exc

        async with TelegramClient(str(self.config.admin_session), self.config.api_id, self.config.api_hash) as command_client, TelegramClient(str(self.config.admin_worker_session), self.config.api_id, self.config.api_hash) as worker_client:
            self.command_client = command_client
            self.worker_client = worker_client
            self.client = worker_client
            command_client.parse_mode = "html"
            worker_client.parse_mode = "html"
            me = await command_client.get_me()
            self.self_id = int(getattr(me, "id", 0) or 0)
            self.plugin_manager.load_admin_plugins()
            # FIX ARCH-03: also discover core plugins for stats (but don't register hooks in admin process)
            self.plugin_manager.load_core_plugins()
            await self.plugin_manager.run_healthchecks()
            self.register_handlers(command_client)
            self.db.log_event("INFO", "ADMIN", f"TR 管理器 Admin 已启动 v{__version__}")
            await self.bootstrap_startup_snapshot_if_needed()
            sync_snapshot_to_config(self.config.work_dir, self.db)
            await self.send_startup_notification()

            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, self.stop_event.set)
                except NotImplementedError:
                    pass

            self.scheduler = AdminScheduler(self)
            tasks = [
                asyncio.create_task(self.scheduler.run()),
                asyncio.create_task(command_client.run_until_disconnected()),
                asyncio.create_task(self.stop_event.wait()),
            ]
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            self.stop_event.set()
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            self.db.log_event("INFO", "ADMIN", "TR 管理器 Admin 正在停止")
            self.logger.info("TR 管理器 Admin stopping")

    async def bootstrap_startup_snapshot_if_needed(self) -> None:
        rows = self.db.list_folders()
        need_bootstrap = not rows or any(row["folder_id"] is None for row in rows)
        if not need_bootstrap:
            total_cache = sum(self.db.count_cache_all_folders().values())
            need_bootstrap = total_cache == 0
        if not need_bootstrap:
            self._startup_sync_note = "启动前校准：已跳过，当前缓存完整。"
            return
        try:
            assert self.worker_client is not None
            sync_report = await sync_dialog_folders(self.worker_client, self.db, self.config)
            route_report = await scan_auto_routes(self.worker_client, self.db, self.config)
            self.last_sync_result = (sync_report, route_report)
            self._startup_sync_note = f"启动前校准：新增分组 {len(sync_report.discovered)} 个，缓存目标 {sum(sync_report.active.values())} 个。"
        except Exception as exc:
            self._startup_sync_note = f"启动前校准失败：{exc}"
            self.db.log_event("ERROR", "SYNC", self._startup_sync_note)

    def register_handlers(self, client: TelegramClient) -> None:
        @client.on(events.NewMessage)
        async def control_panel(event: events.NewMessage.Event) -> None:
            text = (event.raw_text or "").strip()
            if not text or not text.startswith(self.config.cmd_prefix):
                return
            self.db.log_event("INFO", "CMD_SEEN", text[:200])
            if not await self.is_saved_messages_command(event):
                self.db.log_event("INFO", "CMD_DROP", "not_saved_messages_or_not_self")
                return
            match = re.match(rf"^{re.escape(self.config.cmd_prefix)}(\w+)[ \t]*([\s\S]*)", text, re.IGNORECASE)
            if not match:
                self.db.log_event("INFO", "CMD_DROP", "bad_pattern")
                return
            command = match.group(1).lower()
            args = (match.group(2) or "").strip()
            trace = self._make_trace_id()
            setattr(event, "_tgr_trace", trace)
            self.last_command_ts = time.monotonic()
            self.logger.info("[CMD_RX] trace=%s command=%s args=%s", trace, command, args[:200])
            self.db.log_event("INFO", "CMD_ACCEPTED", f"{trace} {command} {args[:160]}")
            if self.plugin_manager.is_heavy_command(command):
                await self.quick_ack(event, command)

            async def _runner() -> None:
                try:
                    handled = await self.plugin_manager.dispatch_admin_command(command, self, event, args)
                    if not handled:
                        await self.safe_reply(event, panel("TR 管理器 · 未知命令", [section("下一步", [f"· 发送 <code>{escape(self.config.cmd_prefix)}help</code> 查看命令总表。"])]))
                except Exception as exc:
                    self.logger.exception("command failed trace=%s: %s", trace, exc)
                    self.db.log_event("ERROR", "COMMAND", f"{trace} {exc}")
                    await self.safe_reply(
                        event,
                        panel(
                            "TR 管理器 · 命令执行异常",
                            [section("异常说明", [blockquote_preview(str(exc), 500)]), section("定位信息", [bullet("跟踪号", trace, code=False)])],
                            "<i>详细堆栈已写入 admin.log，可在终端执行 <code>TR logs admin</code> 排查。</i>",
                        ),
                        auto_delete=0,
                    )
            self.spawn_task(_runner())

    async def is_saved_messages_command(self, event: events.NewMessage.Event) -> bool:
        if not getattr(event, "is_private", False):
            return False
        try:
            chat_id = int(getattr(event, "chat_id", 0) or 0)
            sender_id = int(getattr(event, "sender_id", 0) or 0)
            if self.self_id and chat_id == self.self_id and sender_id == self.self_id:
                return True
        except Exception:
            pass
        try:
            chat = await asyncio.wait_for(event.get_chat(), timeout=1.5)
            return bool(getattr(chat, "self", False))
        except Exception:
            return False

    def _make_trace_id(self) -> str:
        return datetime.now().strftime("cmd-%m%d-%H%M%S-%f")[:-3]

    def _event_trace(self, event: events.NewMessage.Event) -> str:
        return str(getattr(event, "_tgr_trace", "cmd-unknown"))

    async def quick_ack(self, event: events.NewMessage.Event, command: str) -> None:
        trace = self._event_trace(event)
        text = panel(
            "TR 管理器 · 已接收任务",
            [section("调度状态", [bullet("命令", command), bullet("跟踪号", trace, code=False), bullet("说明", "前台已接收，后台继续处理。", code=False)])],
            f"<i>如长时间无结果，可发送 <code>{escape(self.config.cmd_prefix)}jobs</code> 查看后台队列。</i>",
        )
        try:
            assert self.command_client is not None
            await self.command_client.edit_message("me", event.id, text)
            self.db.log_event("INFO", "CMD_ACK", f"{trace} {command}")
        except Exception as exc:
            self.logger.warning("quick_ack failed: %s", exc)
            self.db.log_event("ERROR", "CMD_REPLY_FAIL", f"quick_ack failed: {exc}")
            await self.safe_reply(event, text, auto_delete=0, prefer_edit=False)

    async def run_sync_command(self, event: events.NewMessage.Event) -> None:
        result = self.command_bus.submit(
            "sync_manual",
            payload={"reply_to": int(event.id), "trace": self._event_trace(event)},
            priority=10,
            dedupe_key="sync_manual",
            origin="telegram",
            visible=True,
            delay_seconds=self.config.manual_heavy_delay_seconds,
        )
        self.db.log_event("INFO", "JOB_QUEUE", f"{self._event_trace(event)} manual sync queued")
        title = "TR 管理器 · 同步任务已接收" if result.created else "TR 管理器 · 同步任务已在后台等待"
        body = ["· 已进入后台调度。", "· 前台不会被全量同步阻塞。", "· 完成后会直接回写到当前消息。"]
        await self.safe_reply(event, panel(title, [section("调度层已接收", body)]), auto_delete=0)

    async def run_update_command(self, event: events.NewMessage.Event) -> None:
        if not (self.config.work_dir / ".git").exists():
            await self.safe_reply(event, panel("TR 管理器 · 当前目录不是 Git 仓库", [section("提示", ["· 请使用 Git 方式部署后再执行 update。"])]), auto_delete=0)
            return
        result = self.command_bus.submit(
            "update_repo",
            payload={"reply_to": int(event.id), "trace": self._event_trace(event)},
            priority=15,
            dedupe_key="update_repo",
            origin="telegram",
            visible=True,
            delay_seconds=self.config.update_delay_seconds,
        )
        self.db.log_event("INFO", "JOB_QUEUE", f"{self._event_trace(event)} update_repo queued")
        title = "TR 管理器 · 更新任务已接收" if result.created else "TR 管理器 · 更新任务已在后台等待"
        await self.safe_reply(event, panel(title, [section("后台执行", ["· 正在执行核心仓库与插件仓库更新检查。", "· 如拉取成功，结果会直接回写到当前消息。", "· 需要时可继续下发 restart 任务。"])]), auto_delete=0)

    def queue_snapshot_flush(self) -> None:
        now = time.monotonic()
        if now - getattr(self, "_last_snapshot_queued_at", 0.0) < self.config.snapshot_flush_debounce_seconds:
            return
        self._last_snapshot_queued_at = now
        self.command_bus.submit(
            "config_snapshot_flush",
            priority=220,
            dedupe_key="config_snapshot_flush",
            origin="system",
            visible=False,
            delay_seconds=self.config.snapshot_flush_debounce_seconds,
        )

    def queue_core_reload(self, reason: str, detail: str = "") -> None:
        self.command_bus.submit(
            "reload_core",
            payload={"reason": reason, "detail": detail},
            priority=40,
            dedupe_key="reload_core",
            origin="system",
            visible=False,
            delay_seconds=self.config.reload_debounce_seconds,
        )

    async def after_job(self, job: AdminJob, result: JobResult) -> None:
        if job.kind == "sync_manual":
            sync_report, route_report = self.last_sync_result or (None, None)
            reply_to = int(job.payload.get("reply_to") or 0)
            if sync_report is not None and route_report is not None and reply_to:
                await self.edit_message_by_id(reply_to, self.render_sync_message(sync_report, route_report))
            return
        if job.kind == "sync_auto" and result.notify:
            sync_report, route_report = self.last_sync_result or (None, None)
            if sync_report is not None and route_report is not None:
                await self.send_sync_report(sync_report, route_report, automatic=True)
            return
        if job.kind == "route_scan":
            reply_to = int(job.payload.get("reply_to") or 0)
            route_report = (result.payload or {}).get("route_report")
            if reply_to and route_report is not None:
                queued = sum(route_report.queued.values())
                created = len(route_report.created)
                await self.edit_message_by_id(reply_to, panel("TR 管理器 · 自动归纳扫描完成", [section("执行结果", [bullet("新建分组", created), bullet("排队补群", queued), bullet("空结果规则", len(route_report.matched_zero)), bullet("错误数量", len(route_report.errors))])]))
            return
        if job.kind == "update_repo":
            reply_to = int(job.payload.get("reply_to") or 0)
            title = "TR 管理器 · 代码更新完成" if result.status == "done" else "TR 管理器 · 代码更新失败"
            body = [section("执行结果", [blockquote_preview(result.detail or result.summary, 1400)])]
            footer = f"<i>如需加载最新代码，请继续执行 <code>{escape(self.config.cmd_prefix)}restart</code>。</i>" if result.status == "done" else None
            if reply_to:
                await self.edit_message_by_id(reply_to, panel(title, body, footer))
            return
        if job.kind == "restart_services":
            reply_to = int(job.payload.get("reply_to") or 0)
            if reply_to:
                await self.edit_message_by_id(reply_to, panel("TR 管理器 · 即将重启", [section("调度层", ["· 重启指令已经下发给 systemd。", "· Admin / Core 会自动重新拉起。", "· 未完成的自动归纳任务会继续保留。"])]))
            return

    async def notify_job_failure(self, job: AdminJob, exc: Exception) -> None:
        reply_to = int(job.payload.get("reply_to") or 0)
        if reply_to:
            await self.edit_message_by_id(reply_to, panel("TR 管理器 · 后台任务执行失败", [section("异常说明", [blockquote_preview(str(exc), 500)])], "<i>详细堆栈已写入 admin.log。</i>"))

    async def edit_message_by_id(self, msg_id: int, text: str) -> None:
        if not self.command_client or not msg_id:
            return
        try:
            await self.command_client.edit_message("me", msg_id, text)
        except Exception as exc:
            self.logger.warning("edit_message_by_id failed: %s", exc)
            self.db.log_event("ERROR", "CMD_REPLY_FAIL", f"edit_message failed: {exc}")
            try:
                await self.command_client.send_message("me", text, link_preview=False)
            except Exception:
                pass

    async def apply_route_task(self, task: RouteTask) -> None:
        assert self.worker_client is not None
        req = await self.worker_client(functions.messages.GetDialogFiltersRequest())
        folders = [f for f in getattr(req, "filters", []) if isinstance(f, types.DialogFilter)]
        target = None
        for folder in folders:
            title = dialog_filter_title(folder)
            if (task.folder_id is not None and int(folder.id) == int(task.folder_id)) or title == task.folder_name:
                target = folder
                break
        peers = []
        for peer_id in task.peer_ids:
            try:
                peers.append(await self.worker_client.get_input_entity(peer_id))
            except Exception:
                continue
        if not peers:
            return
        if target is None:
            folder_id = task.folder_id or 2
            used_ids = {int(f.id) for f in folders}
            while folder_id in used_ids:
                folder_id += 1
            new_filter = types.DialogFilter(id=folder_id, title=task.folder_name, pinned_peers=[], include_peers=peers[:100], exclude_peers=[], contacts=False, non_contacts=False, groups=False, broadcasts=False, bots=False, exclude_muted=False, exclude_read=False, exclude_archived=False)
            await self.worker_client(functions.messages.UpdateDialogFilterRequest(id=folder_id, filter=new_filter))
            # FIX BUG-06: update DB with actual folder_id used
            self.db.upsert_folder(task.folder_name, folder_id)
            return
        current_ids = set()
        for peer in getattr(target, "include_peers", []):
            try:
                current_ids.add(int(utils.get_peer_id(peer)))
            except Exception:
                continue
        existing = list(getattr(target, "include_peers", []))
        for peer in peers:
            try:
                pid = int(utils.get_peer_id(peer))
            except Exception:
                continue
            if pid in current_ids:
                continue
            existing.append(peer)
            current_ids.add(pid)
            if len(existing) >= 100:
                break
        target.include_peers = existing[:100]
        await self.worker_client(functions.messages.UpdateDialogFilterRequest(id=target.id, filter=target))

    async def send_sync_report(self, sync_report: SyncReport, route_report: RouteReport, automatic: bool = False) -> None:
        assert self.worker_client is not None
        target = self.config.notify_channel_id if self.config.notify_channel_id is not None else "me"
        text = self.render_sync_message(sync_report, route_report, title_prefix="自动" if automatic else "手动")
        await self.worker_client.send_message(target, text, link_preview=False)

    async def send_startup_notification(self) -> None:
        assert self.worker_client is not None
        target = self.config.notify_channel_id if self.config.notify_channel_id is not None else "me"
        rows = self.db.list_folders()
        enabled_rows = [row for row in rows if int(row["enabled"]) == 1]
        # PERF-02: batch counts
        cache_counts = self.db.count_cache_all_folders()
        rule_counts = self.db.count_rules_all_folders()
        total_cached = sum(cache_counts.values())
        enabled_cached = sum(cache_counts.get(row["folder_name"], 0) for row in enabled_rows)
        target_map, valid_rules = self.db.build_target_map(self.config.global_alert_channel_id)
        folder_blocks = []
        for row in rows[:12]:
            fn = row["folder_name"]
            gc = cache_counts.get(fn, 0)
            rc = rule_counts.get(fn, 0)
            folder_blocks.append(f"· {escape(fn)} · 监听 <code>{'开启' if int(row['enabled']) == 1 else '关闭'}</code> · 群组 <code>{gc}</code> · 规则 <code>{rc}</code>")
        if not folder_blocks:
            folder_blocks = ["· <i>当前还没有任何分组记录。</i>"]
        await self.plugin_manager.run_healthchecks()
        admin_loaded = sum(1 for item in self.plugin_manager.list_plugins('admin') if item.loaded)
        core_loaded = sum(1 for item in self.plugin_manager.list_plugins('core') if item.loaded)
        load_errors = len([x for x in self.plugin_manager.list_plugins() if x.load_error])
        plugin_summary = [
            bullet("Admin 插件", admin_loaded),
            bullet("Core 插件", core_loaded),
            bullet("加载错误", load_errors),
        ]
        text = panel(
            "TR 管理器 · 启动通知",
            [
                section("系统概览", [bullet("版本", __version__), bullet("运行模式", self.config.operation_mode), bullet("命令前缀", self.config.cmd_prefix), bullet("终端管理器", "TR", code=False)]),
                section("监控规模", [bullet("全部分组", len(rows)), bullet("已启用分组", len(enabled_rows)), bullet("缓存群组总量", total_cached), bullet("已启用缓存量", enabled_cached), bullet("监听目标总量", len(target_map)), bullet("生效规则", valid_rules), bullet("自动归纳规则", len(self.db.list_routes()))]),
                section("启动前校准", [self._startup_sync_note or "· 启动前校准：未执行。"]),
                section("插件状态", plugin_summary),
                section("分组总览", folder_blocks),
            ],
            "<i>Telegram 控制台请在收藏夹发送命令，例如 <code>{0}help</code>、<code>{0}status</code>、<code>{0}plugins</code>。</i>".format(escape(self.config.cmd_prefix)),
        )
        await self.worker_client.send_message(target, text, link_preview=False)

    async def delete_later(self, msg: Any, delay: int) -> None:
        if delay <= 0:
            return
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass

    async def safe_reply(self, event: events.NewMessage.Event, text: str, auto_delete: int | None = None, prefer_edit: bool = True, recycle_source_on_reply: bool = True) -> None:
        assert self.command_client is not None
        msg = None
        if prefer_edit:
            try:
                msg = await self.command_client.edit_message("me", event.id, text)
            except Exception:
                msg = None
        if msg is None:
            msg = await self.command_client.send_message("me", text, reply_to=event.id, link_preview=False)
            if recycle_source_on_reply and self.config.recycle_fallback_command_seconds > 0:
                try:
                    src = await self.command_client.get_messages("me", ids=event.id)
                    if src:
                        self.spawn_task(self.delete_later(src, self.config.recycle_fallback_command_seconds))
                except Exception:
                    pass
        delay = self.config.panel_auto_delete_seconds if auto_delete is None else auto_delete
        if delay > 0:
            self.spawn_task(self.delete_later(msg, delay))

    async def run_route_scan_command(self, event: events.NewMessage.Event) -> None:
        result = self.command_bus.submit(
            "route_scan",
            payload={"reply_to": int(event.id), "trace": self._event_trace(event)},
            priority=12,
            dedupe_key="route_scan",
            origin="telegram",
            visible=True,
            delay_seconds=self.config.manual_heavy_delay_seconds + 2,
        )
        self.db.log_event("INFO", "JOB_QUEUE", f"{self._event_trace(event)} route_scan queued")
        title = "TR 管理器 · 自动归纳扫描已接收" if result.created else "TR 管理器 · 自动归纳扫描已在后台等待"
        await self.safe_reply(event, panel(title, [section("调度层已接收", ["· 已进入后台调度。", "· 会扫描路由规则并补充分组。", "· 完成后会回写到当前消息。"])]), auto_delete=0)

    # ---- Render methods ----

    def render_jobs_message(self) -> str:
        rows = self.db.list_open_jobs(limit=20)
        if not rows:
            return panel("TR 管理器 · 后台任务队列", [section("当前状态", ["· <i>当前没有排队或执行中的任务。</i>"])])
        blocks = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            trace = str(payload.get("trace") or "")
            when = row["run_after"] or "立即执行"
            blocks.append(f"<b>{escape(row['kind'])}</b>\n· 状态：<code>{escape(row['status'])}</code>\n· 优先级：<code>{row['priority']}</code>\n· 计划执行：<code>{escape(when)}</code>\n" + (f"· 跟踪号：<code>{escape(trace)}</code>\n" if trace else "") + f"· 更新时间：<code>{escape(row['updated_at'])}</code>")
        return panel("TR 管理器 · 后台任务队列", [section("排队 / 执行中", blocks)], "<i>重任务会延迟、合并、串行执行，以保证轻命令优先响应。</i>")

    def render_help_message(self) -> str:
        groups: dict[str, list[str]] = defaultdict(list)
        for spec in self.plugin_manager.command_registry.all():
            if spec.hidden:
                continue
            groups[spec.category].append(f"<code>{escape(self.config.cmd_prefix)}{escape(spec.usage)}</code> · {escape(spec.summary)}")
        sections_list = [section("帮助说明", ["· 帮助内容按已注册插件实时生成。", "· 轻命令直接回复，重命令进入后台调度。", f"· 插件状态请发送 <code>{escape(self.config.cmd_prefix)}plugins</code>。"])]
        for category in sorted(groups):
            sections_list.append(section(category, groups[category]))
        sections_list.append(section("规则输入说明", ["· 空格、英文逗号、中文逗号都会被识别为分隔符。", "· 单个正则表达式会按原样使用。", "· 同名规则默认追加，不会直接覆盖旧词。", "· 分组名、规则名、短语关键词如包含空格，请用引号包起来。"]))
        return panel("TR 管理器 · 命令总表", sections_list, "<i>所有文案与终端管理器口径统一，Telegram 与 Linux 侧统一使用 TR 管理器命名。</i>")

    def render_config_message(self) -> str:
        notify_target = self.config.notify_channel_id if self.config.notify_channel_id is not None else "Saved Messages"
        alert_target = self.config.global_alert_channel_id if self.config.global_alert_channel_id is not None else "未设置"
        return panel("TR 管理器 · 关键配置", [section("通信与路由", [bullet("API_ID", self.config.api_id), bullet("默认告警", alert_target), bullet("系统通知", notify_target), bullet("命令前缀", self.config.cmd_prefix)]), section("运行策略", [bullet("运行模式", self.config.operation_mode), bullet("自动同步", f"每日 {self.config.auto_sync_time}" if self.config.auto_sync_enabled else "已关闭", code=False), bullet("自动归纳", f"每日 {self.config.auto_route_time}" if self.config.auto_route_enabled else "已关闭", code=False), bullet("热更新", "事件驱动"), bullet("临时面板回收", f"{self.config.panel_auto_delete_seconds} 秒"), bullet("命令回执", "轻命令直回 / 重命令排队", code=False)]), section("部署信息", [bullet("服务前缀", self.config.service_name_prefix), bullet("终端管理器", "TR"), bullet("工作目录", shorten_path(self.config.work_dir), code=False), bullet("核心仓库", self.config.repo_url or "未设置", code=False), bullet("插件仓库", self.config.plugins_repo_url or "未设置", code=False)])])

    def render_status_message(self) -> str:
        stats = self.db.get_runtime_stats()
        rows = self.db.list_folders()
        enabled_cnt = sum(1 for row in rows if int(row["enabled"]) == 1)
        target_map, valid_rules = self.db.build_target_map(self.config.global_alert_channel_id)
        queue_size = self.db.pending_route_count()
        cache_counts = self.db.count_cache_all_folders()
        rule_counts = self.db.count_rules_all_folders()
        active_rows = []
        for row in rows:
            if int(row["enabled"]) != 1:
                continue
            fn = row["folder_name"]
            active_rows.append(f"· {escape(fn)} · 群 <code>{cache_counts.get(fn, 0)}</code> · 规则 <code>{rule_counts.get(fn, 0)}</code>")
            if len(active_rows) >= 8:
                break
        if not active_rows:
            active_rows = ["· <i>暂无启用分组。</i>"]
        return panel("TR 管理器 · 详细状态", [section("运行状态", [bullet("系统状态", "稳定运行中", code=False), bullet("持续运行", format_duration((datetime.now() - self.started_at).total_seconds())), bullet("运行模式", self.config.operation_mode), bullet("自动同步", f"每日 {self.config.auto_sync_time}" if self.config.auto_sync_enabled else "已关闭", code=False), bullet("自动归纳", f"每日 {self.config.auto_route_time}" if self.config.auto_route_enabled else "已关闭", code=False), bullet("热更新", "事件驱动"), bullet("路由队列", f"{queue_size} 个任务待处理" if queue_size > 0 else "空闲", code=False)]), section("监控规模", [bullet("活跃分组", f"{enabled_cnt} / {len(rows)}"), bullet("监听目标", f"{len(target_map)} 个群 / 频道"), bullet("生效规则", f"{valid_rules} 条"), bullet("自动归纳规则", f"{len(self.db.list_routes())} 条")]), section("历史统计", [bullet("总计命中", stats.get("total_hits", "0")), bullet("最近命中分组", stats.get("last_hit_folder") or "暂无记录"), bullet("最近命中时间", stats.get("last_hit_time") or "暂无记录")]), section("已启用分组", active_rows)])

    def render_sync_message(self, sync_report: SyncReport, route_report: RouteReport, title_prefix: str = "") -> str:
        folder_rows: list[str] = []
        if sync_report.discovered:
            folder_rows.extend(f"· 新分组：<code>{escape(name)}</code>" for name in sync_report.discovered)
        if sync_report.renamed:
            folder_rows.extend(f"· 改名：<code>{escape(old)}</code> → <code>{escape(new)}</code>" for old, new in sync_report.renamed)
        if sync_report.deleted:
            folder_rows.extend(f"· 删除：<code>{escape(name)}</code>" for name in sync_report.deleted)
        if not folder_rows:
            folder_rows = ["· <i>分组拓扑没有变化。</i>"]
        route_rows: list[str] = []
        for fn in route_report.created:
            route_rows.append(f"· 新建分组：<code>{escape(fn)}</code>")
        for fn, cnt in route_report.queued.items():
            route_rows.append(f"· 排队补群：<code>{escape(fn)}</code> · <code>{cnt}</code>")
        for fn, cnt in route_report.already_in.items():
            route_rows.append(f"· 已在分组：<code>{escape(fn)}</code> · <code>{cnt}</code>")
        for fn in route_report.matched_zero:
            route_rows.append(f"· 没有命中：<code>{escape(fn)}</code>")
        for fn, err in route_report.errors.items():
            route_rows.append(f"· 错误：<code>{escape(fn)}</code> · {escape(err)}")
        if not route_rows:
            route_rows = ["· <i>没有新的自动归纳动作。</i>"]
        title = f"TR 管理器 · {title_prefix}同步完成" if title_prefix else "TR 管理器 · 同步完成"
        return panel(title, [section("同步结果", [bullet("变动状态", "发现变动并已更新" if sync_report.has_changes else "数据无变动", code=False), bullet("耗时", f"{sync_report.elapsed_seconds:.1f} 秒"), bullet("新分组", len(sync_report.discovered)), bullet("改名", len(sync_report.renamed)), bullet("删除", len(sync_report.deleted))]), section("分组变动", folder_rows), section("自动归纳", route_rows)], f"<i>如果发现了新分组，记得发送 <code>{escape(self.config.cmd_prefix)}enable 分组名</code> 开启监控。</i>")

    def render_plugins_message(self) -> str:
        sections_list = []
        for kind, title in (("admin", "Admin 插件"), ("core", "Core 插件")):
            rows = []
            for record in self.plugin_manager.list_plugins(kind):
                state = record.state_label
                rows.append(
                    f"<b>{escape(record.name)}</b> · <code>{escape(state)}</code>\n"
                    f"· 来源：<code>{escape(record.source)}</code>\n"
                    f"· 版本：<code>{escape(record.version)}</code>\n"
                    f"· 命令：<code>{len(record.commands)}</code> · Hook：<code>{len(record.hooks)}</code>\n"
                    f"· 健康：<code>{escape(record.last_health)}</code> · {escape(record.last_health_detail)}"
                    + (f"\n· 最近异常：{escape(record.last_error)}" if record.last_error else "")
                )
            if not rows:
                rows = ["· <i>当前没有已发现的插件。</i>"]
            sections_list.append(section(title, rows))
        return panel("TR 管理器 · 插件状态", sections_list, f"<i>发送 <code>{escape(self.config.cmd_prefix)}reload 插件名</code> 可重载单个插件。</i>")

    def spawn_task(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self.bg_tasks.add(task)
        task.add_done_callback(self.bg_tasks.discard)

    def find_folder(self, query: str) -> str | None:
        rows = self.db.list_folders()
        names = [row["folder_name"] for row in rows]
        if query in names:
            return query
        lower = query.lower()
        for name in names:
            if name.lower() == lower:
                return name
        candidates = [name for name in names if lower in name.lower()]
        return candidates[0] if len(candidates) == 1 else None

    def parse_int_or_none(self, raw: str) -> int | None:
        raw = raw.strip()
        if raw.lower() in {"", "off", "none", "null", "me"}:
            return None
        return int(raw)

    def restart_services(self, delay: float = 0.0) -> None:
        name = self.config.service_name_prefix
        cmd = ["bash", "-lc", f"sleep {delay}; systemctl restart {name}-core {name}-admin"]
        subprocess.Popen(cmd)

    def write_last_message(self, msg_id: int, action: str) -> None:
        path = self.config.work_dir / ".last_msg"
        path.write_text(json.dumps({"chat_id": "me", "msg_id": msg_id, "action": action}, ensure_ascii=False), encoding="utf-8")


async def run(work_dir: Path) -> None:
    app = AdminApp(work_dir)
    await app.run()
