"""TG-Radar 单进程应用。一个 TelegramClient 同时处理命令和消息监控。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

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
from .telegram_client_factory import build_telegram_client
from .telegram_utils import blockquote_preview, bullet, dialog_filter_title, escape, format_duration, html_code, panel, section, shorten_path, soft_kv
from .version import __version__


@dataclass
class RuntimeState:
    target_map: dict[int, list[dict]]
    valid_rules_count: int
    revision: int
    started_at: datetime


def compile_target_map(raw: dict[int, list[dict]], logger) -> dict[int, list[dict]]:
    compiled: dict[int, list[dict]] = {}
    for chat_id, tasks in raw.items():
        for task in tasks:
            rules = []
            for rule_name, pattern_str in task["rules"]:
                try:
                    rules.append((rule_name, re.compile(pattern_str, re.IGNORECASE)))
                except re.error as exc:
                    logger.warning("正则无效 folder=%s rule=%s: %s", task["folder_name"], rule_name, exc)
            if rules:
                compiled.setdefault(chat_id, []).append({"folder_name": task["folder_name"], "alert_channel": task["alert_channel"], "rules": rules})
    return compiled


class RadarApp:
    """单进程应用：命令 + 监控 + 调度，全部在一个 TelegramClient 上运行。"""

    def __init__(self, work_dir: Path) -> None:
        self.config = load_config(work_dir)
        self.logger = setup_logger("tg-radar", self.config.logs_dir / "radar.log")
        self.db = RadarDB(self.config.db_path)
        seed_db_from_legacy_config_if_needed(work_dir, self.db)
        self.started_at = datetime.now()
        self.stop_event = asyncio.Event()
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
        self.state: RuntimeState | None = None

    def _notify_scheduler(self) -> None:
        if self.scheduler:
            self.scheduler.notify_new_job()

    # ── Session ──

    def _ensure_session(self) -> Path:
        session = self.config.session_path.with_suffix(".session")
        if session.exists():
            try:
                import sqlite3
                c = sqlite3.connect(str(session), timeout=5)
                c.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
                c.close()
                return session
            except Exception:
                self.logger.warning("session 损坏，正在删除")
                session.unlink(missing_ok=True)
        raise FileNotFoundError("缺少 session 文件，请执行 TR reauth")

    # ── 运行时状态（关键词监控用）──

    async def reload_runtime_state(self) -> RuntimeState:
        raw, count = self.db.build_target_map(self.config.global_alert_channel_id)
        compiled = compile_target_map(raw, self.logger)
        prev = self.state.started_at if self.state else datetime.now()
        self.state = RuntimeState(target_map=compiled, valid_rules_count=count, revision=self.db.get_revision(), started_at=prev)
        self.logger.info("状态重载 revision=%s chats=%s rules=%s", self.state.revision, len(self.state.target_map), self.state.valid_rules_count)
        return self.state

    # ── 主循环 ──

    async def run(self) -> None:
        self.config.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_session()

        lock_file = self.config.work_dir / ".radar.lock"
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
        try:
            if os.name != "nt":
                import fcntl
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception as exc:
            raise RuntimeError("TG-Radar 已在运行中") from exc

        # 写 PID 文件
        pid_file = self.config.runtime_dir / "radar.pid"
        pid_file.write_text(str(os.getpid()))

        async with build_telegram_client(self.config) as client:
            self.client = client
            client.parse_mode = "html"
            me = await client.get_me()
            self.self_id = int(getattr(me, "id", 0) or 0)
            self.logger.info("已登录 @%s (ID: %s)", getattr(me, "username", "?"), self.self_id)

            # 加载插件
            self.plugin_manager.load_admin_plugins()
            self.plugin_manager.load_core_plugins()
            await self.plugin_manager.run_healthchecks()

            # 初始化运行时状态
            await self.reload_runtime_state()

            # 注册事件 handler
            self._register_handlers(client)

            self.db.log_event("INFO", "STARTUP", f"TG-Radar 已启动 v{__version__}")
            await self._bootstrap()
            sync_snapshot_to_config(self.config.work_dir, self.db)
            await self._send_startup()

            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, self.stop_event.set)
                except NotImplementedError:
                    pass

            self.scheduler = AdminScheduler(self)
            tasks = [
                asyncio.create_task(self.scheduler.run()),
                asyncio.create_task(client.run_until_disconnected()),
                asyncio.create_task(self.stop_event.wait()),
            ]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            self.stop_event.set()
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            self.db.log_event("INFO", "SHUTDOWN", "TG-Radar 正在关闭")
            self.logger.info("正在关闭")
            try:
                pid_file.unlink(missing_ok=True)
            except Exception:
                pass

    # ── 事件注册（单 Client 同时处理命令 + 监控）──

    def _register_handlers(self, client: TelegramClient) -> None:
        # 收藏夹消息：命令 + 转发识别
        @client.on(events.NewMessage(chats=self.self_id, incoming=True, outgoing=True))
        async def on_saved_message(event) -> None:
            try:
                text = (event.raw_text or "").strip()
                if text and text.startswith(self.config.cmd_prefix):
                    self.spawn_task(self._dispatch_command(event, text))
                else:
                    # 非命令消息（转发等）交给插件钩子
                    self.spawn_task(self.plugin_manager.process_core_message(self, event))
            except Exception as exc:
                self.logger.exception("收藏夹消息处理异常: %s", exc)

        # 所有群/频道消息：关键词监控
        @client.on(events.NewMessage(incoming=True))
        async def on_group_message(event) -> None:
            if event.is_private:
                return
            if not (event.is_group or event.is_channel):
                return
            try:
                await self.plugin_manager.process_core_message(self, event)
            except Exception as exc:
                self.logger.exception("群消息处理异常: %s", exc)

    async def _dispatch_command(self, event, text: str) -> None:
        self.db.log_event("INFO", "CMD_SEEN", text[:200])
        m = re.match(rf"^{re.escape(self.config.cmd_prefix)}(\w+)[ \t]*([\s\S]*)", text, re.IGNORECASE)
        if not m:
            return
        command, args = m.group(1).lower(), (m.group(2) or "").strip()
        trace = datetime.now().strftime("cmd-%m%d-%H%M%S-%f")[:-3]
        setattr(event, "_tgr_trace", trace)
        self.last_command_ts = time.monotonic()
        self.db.log_event("INFO", "CMD_ACCEPTED", f"{trace} {command} {args[:160]}")
        if self.plugin_manager.is_heavy_command(command):
            try:
                await self.client.edit_message("me", event.id, panel("TG-Radar · 任务已接收", [section("调度", [bullet("命令", command), bullet("跟踪号", trace, code=False)])]))
            except Exception:
                pass
        try:
            ok = await self.plugin_manager.dispatch_admin_command(command, self, event, args)
            if not ok:
                await self.safe_reply(event, panel("TG-Radar · 未知命令", [section("提示", [f"发送 <code>{escape(self.config.cmd_prefix)}help</code> 查看命令列表"])]))
        except Exception as exc:
            self.logger.exception("命令异常: %s", exc)
            self.db.log_event("ERROR", "COMMAND", f"{trace} {exc}")
            await self.safe_reply(event, panel("TG-Radar · 命令异常", [section("错误", [blockquote_preview(str(exc), 500)]), section("跟踪", [bullet("ID", trace, code=False)])]), auto_delete=0)

    async def _bootstrap(self) -> None:
        rows = self.db.list_folders()
        need = not rows or any(r["folder_id"] is None for r in rows)
        if not need:
            need = sum(self.db.count_cache_all_folders().values()) == 0
        if not need:
            self._startup_sync_note = "缓存完整，已跳过校准。"
            return
        try:
            sr = await sync_dialog_folders(self.client, self.db, self.config)
            rr = await scan_auto_routes(self.client, self.db, self.config)
            self.last_sync_result = (sr, rr)
            await self.reload_runtime_state()
            self._startup_sync_note = f"新增 {len(sr.discovered)} 个分组，缓存 {sum(sr.active.values())} 个目标。"
        except Exception as exc:
            self._startup_sync_note = f"校准失败: {exc}"
            self.db.log_event("ERROR", "SYNC", str(exc))

    # ── 消息工具 ──

    async def safe_reply(self, event, text: str, auto_delete: int | None = None, prefer_edit: bool = True) -> None:
        gcfg = self.plugin_manager.get_plugin_config_file("general", {})
        panel_ttl = int(gcfg.get("panel_auto_delete_seconds", 45))
        recycle_ttl = int(gcfg.get("recycle_command_seconds", 8))
        msg = None
        if prefer_edit:
            try:
                msg = await self.client.edit_message("me", event.id, text)
            except Exception:
                pass
        if msg is None:
            msg = await self.client.send_message("me", text, reply_to=event.id, link_preview=False)
            if recycle_ttl > 0:
                try:
                    src = await self.client.get_messages("me", ids=event.id)
                    if src:
                        self.spawn_task(self._del(src, recycle_ttl))
                except Exception:
                    pass
        delay = panel_ttl if auto_delete is None else auto_delete
        if delay > 0:
            self.spawn_task(self._del(msg, delay))

    async def _del(self, msg, delay: int) -> None:
        if delay <= 0:
            return
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass

    async def edit_message_by_id(self, msg_id: int, text: str) -> None:
        if not msg_id:
            return
        try:
            await self.client.edit_message("me", msg_id, text)
        except Exception:
            try:
                await self.client.send_message("me", text, link_preview=False)
            except Exception:
                pass

    # ── 业务入口 ──

    async def run_sync_command(self, event) -> None:
        r = self.command_bus.submit("sync_manual", payload={"reply_to": int(event.id), "trace": self._event_trace(event)}, priority=10, dedupe_key="sync_manual", origin="telegram", visible=True, delay_seconds=self.config.manual_heavy_delay_seconds)
        await self.safe_reply(event, panel("TG-Radar · 同步" + ("已接收" if r.created else "排队中"), [section("状态", ["已进入后台调度，完成后回写。"])]), auto_delete=0)

    async def run_update_command(self, event) -> None:
        if not (self.config.work_dir / ".git").exists():
            await self.safe_reply(event, panel("TG-Radar · 更新", [section("提示", ["当前目录不是 Git 仓库。"])]), auto_delete=0)
            return
        self.command_bus.submit("update_repo", payload={"reply_to": int(event.id), "trace": self._event_trace(event)}, priority=15, dedupe_key="update_repo", origin="telegram", visible=True, delay_seconds=self.config.update_delay_seconds)
        await self.safe_reply(event, panel("TG-Radar · 更新已接收", [section("状态", ["正在拉取，完成后回写。"])]), auto_delete=0)

    async def run_route_scan_command(self, event) -> None:
        self.command_bus.submit("route_scan", payload={"reply_to": int(event.id), "trace": self._event_trace(event)}, priority=12, dedupe_key="route_scan", origin="telegram", visible=True, delay_seconds=self.config.manual_heavy_delay_seconds + 2)
        await self.safe_reply(event, panel("TG-Radar · 归纳扫描已接收", [section("状态", ["完成后回写。"])]), auto_delete=0)

    def queue_snapshot_flush(self) -> None:
        now = time.monotonic()
        if now - self._last_snapshot_queued_at < self.config.snapshot_flush_debounce_seconds:
            return
        self._last_snapshot_queued_at = now
        self.command_bus.submit("config_snapshot_flush", priority=220, dedupe_key="config_snapshot_flush", origin="system", visible=False, delay_seconds=self.config.snapshot_flush_debounce_seconds)

    def queue_core_reload(self, reason: str, detail: str = "") -> None:
        """直接重载运行时状态，不需要 IPC 信号。"""
        self.command_bus.submit("reload_state", payload={"reason": reason, "detail": detail}, priority=40, dedupe_key="reload_state", origin="system", visible=False, delay_seconds=self.config.reload_debounce_seconds)

    # ── 任务回调 ──

    async def after_job(self, job: AdminJob, result: JobResult) -> None:
        rt = int(job.payload.get("reply_to") or 0)
        if job.kind == "sync_manual":
            sr, rr = self.last_sync_result or (None, None)
            if sr and rr and rt:
                await self.edit_message_by_id(rt, self._render_sync(sr, rr))
        elif job.kind == "sync_auto" and result.notify:
            sr, rr = self.last_sync_result or (None, None)
            if sr and rr:
                await self.client.send_message(self.config.notify_channel_id or "me", self._render_sync(sr, rr, "自动"), link_preview=False)
        elif job.kind == "route_scan" and rt:
            rr = (result.payload or {}).get("route_report")
            if rr:
                await self.edit_message_by_id(rt, panel("TG-Radar · 归纳扫描完成", [section("结果", [bullet("新建", len(rr.created)), bullet("补群", sum(rr.queued.values())), bullet("错误", len(rr.errors))])]))
        elif job.kind == "update_repo":
            ok = result.status == "done"
            changed = (result.payload or {}).get("changed_plugins", [])
            reload_results = []
            if ok and changed:
                for name in changed:
                    rok, rmsg = self.plugin_manager.reload_plugin(name)
                    reload_results.append(f"{'✔' if rok else '✖'} {name}")
                self.logger.info("自动重载 %d 个插件: %s", len(changed), changed)
            raw = result.detail or ""
            pull_rows = []
            for line in raw.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("[core]"):
                    txt = line.replace("[core]", "").strip()
                    pull_rows.append(bullet("核心仓库", "已是最新" if "already up to date" in txt.lower() else "已更新", code=False))
                elif line.startswith("[plugins]"):
                    txt = line.replace("[plugins]", "").strip()
                    pull_rows.append(bullet("插件仓库", "已是最新" if "already up to date" in txt.lower() else "已更新", code=False))
            if not pull_rows:
                pull_rows.append(bullet("结果", "已是最新", code=False))
            secs = [section("仓库状态", pull_rows)]
            if reload_results:
                secs.append(section(f"自动重载 · {len(changed)} 个插件", reload_results))
            elif ok and not changed:
                secs.append(section("插件", ["无文件变更。"]))
            footer = f"<i>核心代码变更需 <code>{escape(self.config.cmd_prefix)}restart</code></i>" if ok and not changed else None
            if rt:
                await self.edit_message_by_id(rt, panel("TG-Radar · 更新" + ("完成" if ok else "失败"), secs, footer))
        elif job.kind == "restart_services" and rt:
            await self.edit_message_by_id(rt, panel("TG-Radar · 即将重启", [section("说明", ["重启指令已下发。"])]))

    async def notify_job_failure(self, job: AdminJob, exc: Exception) -> None:
        rt = int(job.payload.get("reply_to") or 0)
        if rt:
            await self.edit_message_by_id(rt, panel("TG-Radar · 任务失败", [section("异常", [blockquote_preview(str(exc), 500)])]))

    # ── 分组操作 ──

    async def apply_route_task(self, task: RouteTask) -> None:
        req = await self.client(functions.messages.GetDialogFiltersRequest())
        folders = [f for f in getattr(req, "filters", []) if isinstance(f, types.DialogFilter)]
        target = None
        for f in folders:
            if (task.folder_id is not None and int(f.id) == int(task.folder_id)) or dialog_filter_title(f) == task.folder_name:
                target = f
                break
        peers = []
        for pid in task.peer_ids:
            try:
                peers.append(await self.client.get_input_entity(pid))
            except Exception:
                continue
        if not peers:
            return
        if target is None:
            fid = task.folder_id or 2
            used = {int(f.id) for f in folders}
            while fid in used:
                fid += 1
            nf = types.DialogFilter(id=fid, title=task.folder_name, pinned_peers=[], include_peers=peers[:100], exclude_peers=[], contacts=False, non_contacts=False, groups=False, broadcasts=False, bots=False, exclude_muted=False, exclude_read=False, exclude_archived=False)
            await self.client(functions.messages.UpdateDialogFilterRequest(id=fid, filter=nf))
            self.db.upsert_folder(task.folder_name, fid)
            return
        cur = set()
        for p in getattr(target, "include_peers", []):
            try:
                cur.add(int(utils.get_peer_id(p)))
            except Exception:
                pass
        existing = list(getattr(target, "include_peers", []))
        for p in peers:
            try:
                pid = int(utils.get_peer_id(p))
            except Exception:
                continue
            if pid not in cur:
                existing.append(p)
                cur.add(pid)
                if len(existing) >= 100:
                    break
        target.include_peers = existing[:100]
        await self.client(functions.messages.UpdateDialogFilterRequest(id=target.id, filter=target))

    # ── 渲染 ──

    def render_help_message(self) -> str:
        groups: dict[str, list[str]] = defaultdict(list)
        for spec in self.plugin_manager.command_registry.all():
            if spec.hidden:
                continue
            groups[spec.category].append(f"<code>{escape(self.config.cmd_prefix)}{escape(spec.usage)}</code>  {escape(spec.summary)}")
        secs = [section("使用说明", ["命令列表由已加载插件实时生成。", f"插件管理: <code>{escape(self.config.cmd_prefix)}plugins</code>"])]
        for cat in sorted(groups):
            secs.append(section(cat, groups[cat]))
        return panel("TG-Radar · 命令列表", secs)

    def render_config_message(self) -> str:
        c = self.config
        return panel("TG-Radar · 核心配置", [
            section("连接", [bullet("API ID", c.api_id), bullet("告警频道", c.global_alert_channel_id or "未设置"), bullet("通知频道", c.notify_channel_id or "收藏夹"), bullet("命令前缀", c.cmd_prefix)]),
            section("运行", [bullet("模式", c.operation_mode), bullet("服务前缀", c.service_name_prefix)]),
        ])

    def render_status_message(self) -> str:
        stats = self.db.get_runtime_stats()
        rows = self.db.list_folders()
        enabled = sum(1 for r in rows if int(r["enabled"]))
        cc, rc = self.db.count_cache_all_folders(), self.db.count_rules_all_folders()
        state_info = f"监听 {len(self.state.target_map)} 个目标，{self.state.valid_rules_count} 条规则" if self.state else "未初始化"
        active = []
        for r in rows:
            if not int(r["enabled"]):
                continue
            fn = r["folder_name"]
            active.append(f"· {escape(fn)}  群 <code>{cc.get(fn, 0)}</code>  规则 <code>{rc.get(fn, 0)}</code>")
            if len(active) >= 8:
                break
        return panel("TG-Radar · 系统状态", [
            section("运行", [bullet("运行时间", format_duration((datetime.now() - self.started_at).total_seconds())), bullet("模式", self.config.operation_mode), bullet("监控状态", state_info, code=False)]),
            section("分组", [bullet("总计", f"{enabled} / {len(rows)}"), bullet("待处理队列", self.db.pending_route_count())]),
            section("命中", [bullet("总计", stats.get("total_hits", "0")), bullet("最近分组", stats.get("last_hit_folder") or "—"), bullet("最近时间", stats.get("last_hit_time") or "—")]),
            section("已启用", active or ["<i>暂无</i>"]),
        ])

    def _render_sync(self, sr: SyncReport, rr: RouteReport, prefix: str = "") -> str:
        fr = [f"· 新增 <code>{escape(n)}</code>" for n in sr.discovered]
        fr += [f"· 改名 <code>{escape(o)}</code> → <code>{escape(n)}</code>" for o, n in sr.renamed]
        fr += [f"· 删除 <code>{escape(n)}</code>" for n in sr.deleted]
        rrs = [f"· 新建 <code>{escape(n)}</code>" for n in rr.created]
        rrs += [f"· 补群 <code>{escape(n)}</code> × <code>{c}</code>" for n, c in rr.queued.items()]
        t = f"TG-Radar · {prefix}同步完成" if prefix else "TG-Radar · 同步完成"
        return panel(t, [
            section("概览", [bullet("变动", "有" if sr.has_changes else "无", code=False), bullet("耗时", f"{sr.elapsed_seconds:.1f}s")]),
            section("分组变动", fr or ["<i>无变化</i>"]),
            section("自动归纳", rrs or ["<i>无动作</i>"]),
        ])

    def render_jobs_message(self) -> str:
        rows = self.db.list_open_jobs(limit=20)
        if not rows:
            return panel("TG-Radar · 后台队列", [section("状态", ["<i>当前无任务</i>"])])
        blocks = [f"<b>{escape(r['kind'])}</b>  <code>{escape(r['status'])}</code>  优先级 <code>{r['priority']}</code>" for r in rows]
        return panel("TG-Radar · 后台队列", [section("排队中", blocks)])

    def render_plugins_message(self) -> str:
        secs = []
        for kind, title in (("admin", "Admin 插件"), ("core", "Core 插件")):
            rows = []
            for rec in self.plugin_manager.list_plugins(kind):
                rows.append(
                    f"<b>{escape(rec.name)}</b>  <code>{escape(rec.state_label)}</code>\n"
                    f"  来源 <code>{escape(rec.source)}</code>  版本 <code>{escape(rec.version)}</code>\n"
                    f"  命令 <code>{len(rec.commands)}</code>  Hook <code>{len(rec.hooks)}</code>  健康 <code>{escape(rec.last_health)}</code>"
                    + (f"\n  异常: {escape(rec.last_error[:80])}" if rec.last_error else "")
                )
            secs.append(section(title, rows or ["<i>无</i>"]))
        return panel("TG-Radar · 插件状态", secs, f"<i><code>{escape(self.config.cmd_prefix)}reload 名称</code> 重载单个插件</i>")

    async def _send_startup(self) -> None:
        target = self.config.notify_channel_id or "me"
        rows = self.db.list_folders()
        cc, rc = self.db.count_cache_all_folders(), self.db.count_rules_all_folders()
        enabled = [r for r in rows if int(r["enabled"])]
        al = sum(1 for p in self.plugin_manager.list_plugins("admin") if p.loaded)
        cl = sum(1 for p in self.plugin_manager.list_plugins("core") if p.loaded)
        errs = sum(1 for p in self.plugin_manager.list_plugins() if p.load_error)
        state_info = f"监听 {len(self.state.target_map)} 个目标，{self.state.valid_rules_count} 条规则" if self.state else "未初始化"
        fb = []
        for r in rows[:12]:
            fn = r["folder_name"]
            st = "开启" if int(r["enabled"]) else "关闭"
            fb.append(f"· {escape(fn)}  {st}  群 <code>{cc.get(fn, 0)}</code>  规则 <code>{rc.get(fn, 0)}</code>")
        text = panel("TG-Radar · 启动完成", [
            section("系统", [bullet("版本", __version__), bullet("架构", "单进程 · 事件驱动", code=False), bullet("模式", self.config.operation_mode), bullet("前缀", self.config.cmd_prefix)]),
            section("监控", [bullet("分组", f"{len(enabled)} / {len(rows)}"), bullet("缓存", sum(cc.values())), bullet("状态", state_info, code=False)]),
            section("启动校准", [self._startup_sync_note or "未执行"]),
            section("插件", [bullet("Admin", al), bullet("Core", cl), bullet("错误", errs)]),
            section("分组", fb or ["<i>暂无</i>"]),
        ], f"<i>发送 <code>{escape(self.config.cmd_prefix)}help</code> 查看命令列表</i>")
        await self.client.send_message(target, text, link_preview=False)

    # ── 工具 ──

    def spawn_task(self, coro) -> None:
        t = asyncio.create_task(coro)
        self.bg_tasks.add(t)
        t.add_done_callback(self.bg_tasks.discard)

    def find_folder(self, query: str) -> str | None:
        names = [r["folder_name"] for r in self.db.list_folders()]
        if query in names:
            return query
        lo = query.lower()
        for n in names:
            if n.lower() == lo:
                return n
        c = [n for n in names if lo in n.lower()]
        return c[0] if len(c) == 1 else None

    def parse_int_or_none(self, raw: str) -> int | None:
        raw = raw.strip()
        return None if raw.lower() in {"", "off", "none", "null", "me"} else int(raw)

    def restart_services(self, delay: float = 0.0) -> None:
        n = self.config.service_name_prefix
        subprocess.Popen(["bash", "-lc", f"sleep {delay}; systemctl restart {n}"])

    def write_last_message(self, msg_id: int, action: str) -> None:
        (self.config.work_dir / ".last_msg").write_text(json.dumps({"chat_id": "me", "msg_id": msg_id, "action": action}), encoding="utf-8")

    def _event_trace(self, event) -> str:
        return str(getattr(event, "_tgr_trace", "cmd-?"))


async def run(work_dir: Path) -> None:
    await RadarApp(work_dir).run()
