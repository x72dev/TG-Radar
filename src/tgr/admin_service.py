from __future__ import annotations

import asyncio
import html
import json
import os
import re
import shlex
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient, events, functions, types, utils

from .compat import seed_db_from_legacy_config_if_needed
from .config import load_config, sync_snapshot_to_config, update_config_data
from .command_bus import CommandBus
from .db import AdminJob, RadarDB, RouteTask
from .executors import JobResult
from .scheduler import AdminScheduler
from .logger import setup_logger
from .sync_logic import RouteReport, SyncReport, scan_auto_routes, sync_dialog_folders
from .telegram_utils import (
    blockquote_preview,
    bullet,
    dialog_filter_title,
    escape,
    format_duration,
    html_code,
    normalize_pattern_from_terms,
    merge_patterns,
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
        self.logger = setup_logger("tg-radar-admin", self.config.logs_dir / "admin.log")
        self.db = RadarDB(self.config.db_path)
        seed_db_from_legacy_config_if_needed(work_dir, self.db)
        sync_snapshot_to_config(work_dir, self.db)
        self.started_at = datetime.now()
        self.stop_event = asyncio.Event()
        self.client: TelegramClient | None = None
        self.bg_tasks: set[asyncio.Task] = set()
        self.last_command_ts = 0.0
        self.command_bus = CommandBus(self.db, notifier=self._notify_scheduler)
        self.scheduler: AdminScheduler | None = None
        self.last_sync_result: tuple[SyncReport, RouteReport] | None = None
        self.self_id: int | None = None

    def _notify_scheduler(self) -> None:
        if self.scheduler is not None:
            self.scheduler.notify_new_job()

    async def run(self) -> None:
        self.config.sessions_dir.mkdir(parents=True, exist_ok=True)
        if not (self.config.admin_session.with_suffix(".session")).exists():
            raise FileNotFoundError("Missing runtime/sessions/tg_radar_admin.session. Run bootstrap_session.py first.")

        lock_file = self.config.work_dir / ".admin.lock"
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
        try:
            if os.name != "nt":
                import fcntl
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            raise RuntimeError("tg-radar-admin is already running")

        async with TelegramClient(str(self.config.admin_session), self.config.api_id, self.config.api_hash) as client:
            self.client = client
            client.parse_mode = "html"
            me = await client.get_me()
            self.self_id = int(getattr(me, "id", 0) or 0)
            self.register_handlers(client)
            self.db.log_event("INFO", "ADMIN", f"TG-Radar admin started v{__version__}")
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
                asyncio.create_task(client.run_until_disconnected()),
                asyncio.create_task(self.stop_event.wait()),
            ]
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            self.stop_event.set()
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            self.db.log_event("INFO", "ADMIN", "admin service stopping")
            self.logger.info("TG-Radar admin service stopping")

    def register_handlers(self, client: TelegramClient) -> None:
        @client.on(events.NewMessage)
        async def control_panel(event: events.NewMessage.Event) -> None:
            text = (event.raw_text or "").strip()
            if not text:
                return
            if not text.startswith(self.config.cmd_prefix):
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
            self.last_command_ts = time.monotonic()
            self.logger.info("[CMD_RX] command=%s args=%s", command, args[:200])
            self.db.log_event("INFO", "CMD_ACCEPTED", f"{command} {args[:200]}")

            async def _runner() -> None:
                try:
                    await self.dispatch(event, command, args)
                except Exception as exc:
                    self.logger.exception("command failed: %s", exc)
                    self.db.log_event("ERROR", "COMMAND", str(exc))
                    await self.safe_reply(
                        event,
                        panel(
                            "TG-Radar 命令执行异常",
                            [section("异常说明", [blockquote_preview(str(exc), 500)])],
                            "<i>详细堆栈已写入 admin.log，可在终端执行 <code>TR logs admin</code> 排查。</i>",
                        ),
                        auto_delete=0,
                    )
            self.spawn_task(_runner())

    async def is_saved_messages_command(self, event: events.NewMessage.Event) -> bool:
        if not getattr(event, "out", False):
            return False
        if not getattr(event, "is_private", False):
            return False
        try:
            chat = await event.get_chat()
            if getattr(chat, "self", False):
                return True
        except Exception:
            pass
        try:
            chat_id = int(getattr(event, "chat_id", 0) or 0)
            sender_id = int(getattr(event, "sender_id", 0) or 0)
            if self.self_id and chat_id == self.self_id and sender_id == self.self_id:
                return True
        except Exception:
            return False
        return False

    async def dispatch(self, event: events.NewMessage.Event, command: str, args: str) -> None:
        prefix = escape(self.config.cmd_prefix)

        if command == "help":
            await self.safe_reply(event, self.render_help_message(), auto_delete=0)
            return

        if command == "ping":
            stats = self.db.get_runtime_stats()
            await self.safe_reply(
                event,
                panel(
                    "TG-Radar 在线心跳",
                    [section("快速状态", [bullet("管理层运行", format_duration((datetime.now() - self.started_at).total_seconds())), bullet("历史命中", stats.get("total_hits", "0")), bullet("热更新", "事件驱动"), bullet("自动同步", f"{self.config.auto_sync_time} 每日执行" if self.config.auto_sync_enabled else "已关闭", code=False)])],
                ),
                auto_delete=12,
            )
            return

        if command == "status":
            await self.safe_reply(event, self.render_status_message())
            return

        if command == "version":
            await self.safe_reply(
                event,
                panel(
                    "TG-Radar 版本信息",
                    [
                        section("当前构建", [bullet("版本", __version__), bullet("架构", "Plan C / Admin + Core / SQLite WAL"), bullet("终端命令", "TR")]),
                        section("部署位置", [bullet("工作目录", shorten_path(self.config.work_dir), code=False), bullet("会话策略", "编辑原消息 + 临时面板自动回收", code=False)]),
                    ],
                ),
            )
            return

        if command == "config":
            await self.safe_reply(event, self.render_config_message())
            return

        if command == "setnotify":
            value = self.parse_int_or_none(args)
            update_config_data(self.config.work_dir, {"notify_channel_id": value})
            self.config = load_config(self.config.work_dir)
            self.db.log_event("INFO", "SET_NOTIFY", str(value))
            await self.safe_reply(event, panel("系统通知目标已更新", [section("新配置", [bullet("通知去向", value if value is not None else "Saved Messages"), bullet("覆盖范围", "启动 / 同步 / 更新 / 恢复", code=False)])]))
            return

        if command == "setalert":
            value = self.parse_int_or_none(args)
            update_config_data(self.config.work_dir, {"global_alert_channel_id": value})
            self.config = load_config(self.config.work_dir)
            self.queue_core_reload("set_alert", str(value))
            self.db.log_event("INFO", "SET_ALERT", str(value))
            await self.safe_reply(event, panel("默认告警频道已更新", [section("新配置", [bullet("默认告警", value if value is not None else "未设置"), bullet("生效范围", "未单独配置告警频道的分组", code=False)])]))
            return

        if command == "setprefix":
            value = args.strip()
            if not value or len(value) > 3 or " " in value or any(ch in value for ch in ["\\", '"', "'"]):
                await self.safe_reply(event, panel("命令前缀格式无效", [section("输入要求", [bullet("长度", "1-3 个字符", code=False), bullet("限制", "不能包含空格、引号、反斜杠", code=False)])]))
                return
            update_config_data(self.config.work_dir, {"cmd_prefix": value})
            self.db.log_event("INFO", "SET_PREFIX", value)
            self.write_last_message(event.id, "restart")
            await self.safe_reply(event, panel("命令前缀已更新", [section("新前缀", [bullet("命令前缀", value), bullet("试用命令", f"{value}help")])], "<i>接下来会自动重启 Admin / Core，新的前缀会在服务恢复后立刻生效。</i>"), auto_delete=0)
            self.restart_services(delay=1.2)
            return

        if command == "log":
            scope = "important"
            limit = 15
            tokens = shlex.split(args) if args else []
            for token in tokens:
                lower = token.lower()
                if lower in {"all", "raw", "full", "debug"}:
                    scope = "all"
                elif lower in {"important", "key", "critical"}:
                    scope = "important"
                elif lower in {"normal", "recent"}:
                    scope = "normal"
                elif token.isdigit():
                    limit = min(40, max(1, int(token)))
            rows = self.db.recent_logs_for_panel(limit=limit, scope=scope)
            if not rows:
                await self.safe_reply(event, panel("最近关键事件", [section("结果", ["· <i>目前还没有可展示的关键事件。</i>"])]), auto_delete=0)
                return
            blocks = []
            for row in rows:
                detail = row["detail"]
                if len(detail) > 110:
                    detail = detail[:109] + "…"
                lines = [f"{row['icon']} <b>{escape(row['title'])}</b>", soft_kv("时间", row["created_at"]), soft_kv("摘要", row["summary"])]
                if detail and detail != row["summary"]:
                    lines.append(soft_kv("详情", detail))
                blocks.append("\n".join(lines))
            footer = "<i>默认只展示关键事件。发送 <code>{0}log normal 20</code> 可看更多常规事件，发送 <code>{0}log all 20</code> 可看完整事件流。</i>".format(escape(self.config.cmd_prefix))
            await self.safe_reply(event, panel("最近关键事件", [section("事件流", blocks)], footer), auto_delete=0)
            return

        if command == "folders":
            rows = self.db.list_folders()
            if not rows:
                await self.safe_reply(event, panel("TG 分组总览", [section("当前状态", ["· <i>系统里还没有任何分组记录。先执行一次同步，或先在 Telegram 侧创建分组。</i>"])]))
                return
            blocks = []
            for row in rows:
                folder_name = row["folder_name"]
                group_count = self.db.count_cache_for_folder(folder_name)
                rule_count = self.db.count_rules_for_folder(folder_name)
                icon = "🟢" if int(row["enabled"]) == 1 else "⚪"
                blocks.append(f"{icon} <b>{escape(folder_name)}</b>\n· 监听：{html_code('开启' if int(row['enabled']) == 1 else '关闭')}\n· 群数：{html_code(group_count)}\n· 规则：{html_code(rule_count)}")
            await self.safe_reply(event, panel("TG 分组总览", [section("当前分组", blocks)]), auto_delete=max(75, self.config.panel_auto_delete_seconds))
            return

        if command == "rules":
            if not args:
                await self.safe_reply(event, panel("缺少参数", [section("示例", [f"<code>{prefix}rules 示例分组</code>"])]))
                return
            folder = self.find_folder(args)
            if folder is None:
                await self.safe_reply(event, panel("找不到该分组", [section("提示", [f"· 先发送 <code>{prefix}folders</code> 查看系统已识别的分组。"])]))
                return
            rows = self.db.get_rules_for_folder(folder)
            if not rows:
                await self.safe_reply(event, panel(f"{folder} 的规则面板", [section("当前状态", ["· <i>该分组还没有任何启用中的规则。</i>"])]))
                return
            blocks = []
            for row in rows:
                blocks.append(f"<b>{escape(row['rule_name'])}</b>\n· 表达式：<code>{escape(row['pattern'])}</code>\n· 更新时间：<code>{escape(row['updated_at'])}</code>")
            await self.safe_reply(event, panel(f"{folder} 的规则面板", [section("已启用规则", blocks)]), auto_delete=max(80, self.config.panel_auto_delete_seconds))
            return

        if command == "enable":
            if not args:
                await self.safe_reply(event, panel("缺少参数", [section("示例", [f"<code>{prefix}enable 示例分组</code>"])]))
                return
            folder = self.find_folder(args)
            if folder is None:
                await self.safe_reply(event, panel("找不到该分组", [section("提示", [f"· 先发送 <code>{prefix}folders</code> 查看列表。"])]))
                return
            self.db.set_folder_enabled(folder, True)
            self.queue_snapshot_flush()
            self.queue_core_reload("enable_folder", folder)
            self.db.log_event("INFO", "ENABLE_FOLDER", folder)
            await self.safe_reply(event, panel("分组监控已开启", [section("当前动作", [bullet("分组", folder), bullet("状态", "开启")])], "<i>这项变更已直接通知 Core 立即重载，不再依赖短周期轮询。</i>"))
            return

        if command == "disable":
            if not args:
                await self.safe_reply(event, panel("缺少参数", [section("示例", [f"<code>{prefix}disable 示例分组</code>"])]))
                return
            folder = self.find_folder(args)
            if folder is None:
                await self.safe_reply(event, panel("找不到该分组", [section("提示", [f"· 先发送 <code>{prefix}folders</code> 查看列表。"])]))
                return
            self.db.set_folder_enabled(folder, False)
            self.queue_snapshot_flush()
            self.queue_core_reload("disable_folder", folder)
            self.db.log_event("INFO", "DISABLE_FOLDER", folder)
            await self.safe_reply(event, panel("分组监控已关闭", [section("当前动作", [bullet("分组", folder), bullet("状态", "关闭")])], "<i>对应监听目标已通知 Core 立即重载，新的匹配范围会尽快生效。</i>"))
            return

        if command == "addrule":
            tokens = shlex.split(args)
            if len(tokens) < 3:
                await self.safe_reply(event, panel("参数不足", [section("示例", [f"<code>{prefix}addrule 示例分组 规则A 监控词A 监控词B</code>", f"<code>{prefix}addrule 示例分组 规则A 项目(?:A|B|C)</code>", f"<code>{prefix}setrule 示例分组 规则A 全量表达式</code>"])]))
                return
            folder = self.find_folder(tokens[0]) or tokens[0]
            rule_name = tokens[1]
            incoming_pattern = normalize_pattern_from_terms(tokens[2:])
            if self.db.get_folder(folder) is None:
                self.db.upsert_folder(folder, None, enabled=False)
            existing_rows = self.db.get_rules_for_folder(folder)
            existing_rule = next((row for row in existing_rows if row["rule_name"] == rule_name), None)
            merged_pattern = merge_patterns(existing_rule["pattern"] if existing_rule else None, incoming_pattern)
            self.db.upsert_rule(folder, rule_name, merged_pattern)
            self.queue_snapshot_flush()
            self.queue_core_reload("add_rule", f"{folder}/{rule_name}")
            action = "UPDATE_RULE" if existing_rule else "ADD_RULE"
            self.db.log_event("INFO", action, f"{folder}/{rule_name} -> {merged_pattern}")
            new_terms = split_terms(tokens[2:])
            new_terms_preview = " / ".join(new_terms[:6]) + ("…" if len(new_terms) > 6 else "")
            await self.safe_reply(
                event,
                panel(
                    "规则已追加",
                    [section("规则详情", [bullet("分组", folder), bullet("规则名", rule_name), bullet("新增词项", new_terms_preview, code=False), bullet("当前表达式", merged_pattern)])],
                    "<i>同名规则默认追加新词并自动去重。若要整条覆盖，请使用 <code>{0}setrule</code>。</i>".format(prefix),
                ),
            )
            return

        if command == "setrule":
            tokens = shlex.split(args)
            if len(tokens) < 3:
                await self.safe_reply(event, panel("参数不足", [section("示例", [f"<code>{prefix}setrule 示例分组 规则A 新表达式</code>"])]))
                return
            folder = self.find_folder(tokens[0]) or tokens[0]
            rule_name = tokens[1]
            pattern = normalize_pattern_from_terms(tokens[2:])
            if self.db.get_folder(folder) is None:
                self.db.upsert_folder(folder, None, enabled=False)
            self.db.upsert_rule(folder, rule_name, pattern)
            self.queue_snapshot_flush()
            self.queue_core_reload("set_rule", f"{folder}/{rule_name}")
            self.db.log_event("INFO", "UPDATE_RULE", f"{folder}/{rule_name} -> {pattern}")
            await self.safe_reply(event, panel("规则已覆盖", [section("规则详情", [bullet("分组", folder), bullet("规则名", rule_name), bullet("表达式", pattern)])]))
            return

        if command == "delrule":
            tokens = shlex.split(args)
            if len(tokens) < 2:
                await self.safe_reply(event, panel("参数不足", [section("示例", [f"<code>{prefix}delrule 示例分组 规则A</code>", f"<code>{prefix}delrule 示例分组 规则A 监控词A</code>"])]))
                return
            folder = self.find_folder(tokens[0]) or tokens[0]
            rule_name = tokens[1]
            terms = tokens[2:]
            rows = self.db.get_rules_for_folder(folder)
            rule = next((row for row in rows if row["rule_name"] == rule_name), None)
            if rule is None:
                await self.safe_reply(event, panel("找不到该规则", [section("定位信息", [bullet("分组", folder), bullet("规则名", rule_name)])]))
                return
            if not terms:
                self.db.delete_rule(folder, rule_name)
                self.queue_snapshot_flush()
                self.queue_core_reload("delete_rule", f"{folder}/{rule_name}")
                self.db.log_event("INFO", "DELETE_RULE", f"{folder}/{rule_name}")
                await self.safe_reply(event, panel("规则已删除", [section("删除结果", [bullet("分组", folder), bullet("规则名", rule_name)])]))
                return
            new_pattern = try_remove_terms_from_pattern(rule["pattern"], terms)
            if not new_pattern:
                self.db.delete_rule(folder, rule_name)
                self.queue_snapshot_flush()
                self.queue_core_reload("clear_rule", f"{folder}/{rule_name}")
                await self.safe_reply(event, panel("规则已清空", [section("删除结果", [bullet("分组", folder), bullet("规则名", rule_name)])]))
                return
            self.db.update_rule_pattern(folder, rule_name, new_pattern)
            self.queue_snapshot_flush()
            self.queue_core_reload("update_rule", f"{folder}/{rule_name}")
            self.db.log_event("INFO", "UPDATE_RULE", f"{folder}/{rule_name} -> {new_pattern}")
            await self.safe_reply(event, panel("规则已更新", [section("新表达式", [f"<code>{escape(new_pattern)}</code>"])]))
            return

        if command == "routes":
            rows = self.db.list_routes()
            if not rows:
                await self.safe_reply(event, panel("自动收纳规则面板", [section("当前状态", ["· <i>当前没有自动收纳规则。</i>"])]))
                return
            blocks = []
            for row in rows:
                blocks.append(f"<b>{escape(row['folder_name'])}</b>\n· 路由表达式：<code>{escape(row['pattern'])}</code>\n· 更新时间：<code>{escape(row['updated_at'])}</code>")
            await self.safe_reply(event, panel("自动收纳规则面板", [section("当前规则", blocks)]), auto_delete=max(75, self.config.panel_auto_delete_seconds))
            return

        if command == "addroute":
            tokens = shlex.split(args)
            if len(tokens) < 2:
                await self.safe_reply(event, panel("参数不足", [section("示例", [f"<code>{prefix}addroute 示例分组 标题词A 标题词B</code>", f"<code>{prefix}addroute 示例分组 项目(?:A|B|C)</code>"])]))
                return
            folder = self.find_folder(tokens[0]) or tokens[0]
            if self.db.get_folder(folder) is None:
                self.db.upsert_folder(folder, None, enabled=False)
            pattern = normalize_pattern_from_terms(tokens[1:])
            self.db.set_route(folder, pattern)
            self.queue_snapshot_flush()
            self.db.log_event("INFO", "ADD_ROUTE", f"{folder} -> {pattern}")
            await self.safe_reply(event, panel("自动收纳规则已保存", [section("规则详情", [bullet("分组", folder), bullet("路由表达式", pattern)])], "<i>后续自动同步会持续扫描新群，并把命中的目标加入路由补群队列。</i>"))
            return

        if command == "delroute":
            if not args:
                await self.safe_reply(event, panel("参数不足", [section("示例", [f"<code>{prefix}delroute 示例分组</code>"])]))
                return
            folder = self.find_folder(args) or args.strip()
            if not self.db.delete_route(folder):
                await self.safe_reply(event, panel("没有找到该自动收纳规则", [section("定位信息", [bullet("分组", folder)])]))
                return
            self.queue_snapshot_flush()
            self.db.log_event("INFO", "DELETE_ROUTE", folder)
            await self.safe_reply(event, panel("自动收纳规则已删除", [section("删除结果", [bullet("分组", folder)])]))
            return

        if command == "sync":
            await self.run_sync_command(event)
            return

        if command == "routescan":
            await self.run_route_scan_command(event)
            return

        if command == "jobs":
            await self.safe_reply(event, self.render_jobs_message(), auto_delete=0)
            return

        if command == "restart":
            self.write_last_message(event.id, "restart")
            result = self.command_bus.submit(
                "restart_services",
                payload={"reply_to": int(event.id), "delay": self.config.restart_delay_seconds},
                priority=20,
                dedupe_key="restart_services",
                origin="telegram",
                visible=True,
                delay_seconds=self.config.restart_delay_seconds,
            )
            self.db.log_event("INFO", "JOB_QUEUE", "restart_services queued")
            title = "重启任务已排队" if result.created else "重启任务已在后台执行"
            await self.safe_reply(event, panel(title, [section("执行说明", [bullet("影响范围", "Admin / Core 双服务", code=False), bullet("任务状态", "数据库中未完成的路由任务会继续保留", code=False), bullet("处理方式", "调度层会在空闲时下发重启指令", code=False)])]), auto_delete=0)
            return

        if command == "update":
            self.write_last_message(event.id, "update")
            await self.run_update_command(event)
            return

        await self.safe_reply(event, panel("未知命令", [section("下一步", [f"· 发送 <code>{prefix}help</code> 查看可用命令。"])]))

    async def run_sync_command(self, event: events.NewMessage.Event) -> None:
        result = self.command_bus.submit(
            "sync_manual",
            payload={"reply_to": int(event.id)},
            priority=10,
            dedupe_key="sync_manual",
            origin="telegram",
            visible=True,
            delay_seconds=self.config.manual_heavy_delay_seconds,
        )
        self.db.log_event("INFO", "JOB_QUEUE", "manual sync queued")
        title = "同步任务已排队" if result.created else "同步任务已在后台等待"
        body = ["· 已收到命令，前台不会被重任务阻塞", "· 系统会延迟几秒后再启动全量同步", "· 完成后会把结果回写到当前消息"]
        await self.safe_reply(event, panel(title, [section("调度层已接收", body)]), auto_delete=0)

    async def run_update_command(self, event: events.NewMessage.Event) -> None:
        if not (self.config.work_dir / ".git").exists():
            await self.safe_reply(event, panel("当前目录不是 git 仓库", [section("提示", ["· 请使用 git 方式部署后再执行 update。"])]), auto_delete=0)
            return
        result = self.command_bus.submit(
            "update_repo",
            payload={"reply_to": int(event.id)},
            priority=15,
            dedupe_key="update_repo",
            origin="telegram",
            visible=True,
            delay_seconds=self.config.update_delay_seconds,
        )
        self.db.log_event("INFO", "JOB_QUEUE", "update_repo queued")
        title = "更新任务已排队" if result.created else "更新任务已在后台等待"
        await self.safe_reply(event, panel(title, [section("后台执行", ["· 正在执行 git pull --ff-only", "· 如拉取成功，结果会直接回写到当前消息", "· 需要时可继续下发 restart 任务"])]), auto_delete=0)

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
                await self.edit_message_by_id(reply_to, panel("自动收纳扫描完成", [section("执行结果", [bullet("新建分组", created), bullet("排队补群", queued), bullet("空结果规则", len(route_report.matched_zero)), bullet("错误数量", len(route_report.errors))])]))
            return
        if job.kind == "update_repo":
            reply_to = int(job.payload.get("reply_to") or 0)
            title = "代码更新完成" if result.status == "done" else "代码更新失败"
            body = [section("执行结果", [blockquote_preview(result.detail or result.summary, 1400)])]
            footer = "<i>如需加载最新代码，请继续执行 <code>{}restart</code>。</i>".format(escape(self.config.cmd_prefix)) if result.status == "done" else None
            if reply_to:
                await self.edit_message_by_id(reply_to, panel(title, body, footer))
            return
        if job.kind == "restart_services":
            reply_to = int(job.payload.get("reply_to") or 0)
            if reply_to:
                await self.edit_message_by_id(reply_to, panel("TG-Radar 即将重启", [section("调度层", ["· 重启指令已经下发给 systemd", "· Admin / Core 会自动重新拉起", "· 未完成的自动收纳任务会继续保留"])]))
            return

    async def notify_job_failure(self, job: AdminJob, exc: Exception) -> None:
        reply_to = int(job.payload.get("reply_to") or 0)
        if reply_to:
            await self.edit_message_by_id(reply_to, panel("后台任务执行失败", [section("异常说明", [blockquote_preview(str(exc), 500)])], "<i>详细堆栈已写入 admin.log，可在终端执行 <code>TR logs admin</code> 排查。</i>"))

    async def edit_message_by_id(self, msg_id: int, text: str) -> None:
        if not self.client or not msg_id:
            return
        try:
            await self.client.edit_message("me", msg_id, text)
        except Exception as exc:
            self.logger.warning("edit_message_by_id failed: %s", exc)
            self.db.log_event("ERROR", "COMMAND", f"edit_message failed: {exc}")

    async def apply_route_task(self, task: RouteTask) -> None:
        assert self.client is not None
        req = await self.client(functions.messages.GetDialogFiltersRequest())
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
                peers.append(await self.client.get_input_entity(peer_id))
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
            await self.client(functions.messages.UpdateDialogFilterRequest(id=folder_id, filter=new_filter))
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
        await self.client(functions.messages.UpdateDialogFilterRequest(id=target.id, filter=target))

    async def send_sync_report(self, sync_report: SyncReport, route_report: RouteReport, automatic: bool = False) -> None:
        assert self.client is not None
        target = self.config.notify_channel_id if self.config.notify_channel_id is not None else "me"
        title = "TG-Radar 自动同步报告" if automatic else "TG-Radar 手动同步报告"
        status = "发现变动并已更新" if sync_report.has_changes or route_report.queued or route_report.created else "同步完成，数据无变动"

        folder_rows: list[str] = []
        if sync_report.discovered:
            folder_rows.extend(f"· 新分组：<code>{escape(name)}</code>" for name in sync_report.discovered)
        if sync_report.renamed:
            folder_rows.extend(f"· 改名：<code>{escape(old)}</code> → <code>{escape(new)}</code>" for old, new in sync_report.renamed)
        if sync_report.deleted:
            folder_rows.extend(f"· 删除：<code>{escape(name)}</code>" for name in sync_report.deleted)
        if not folder_rows:
            folder_rows.append("· <i>本次没有新增、改名或删除任何分组。</i>")

        route_rows: list[str] = []
        for name in route_report.created:
            route_rows.append(f"· 新建分组：<code>{escape(name)}</code>")
        for name, cnt in route_report.queued.items():
            route_rows.append(f"· 排队补群：<code>{escape(name)}</code> · <code>{cnt}</code>")
        for name, cnt in route_report.already_in.items():
            route_rows.append(f"· 已在分组：<code>{escape(name)}</code> · <code>{cnt}</code>")
        for name in route_report.matched_zero:
            route_rows.append(f"· 没有命中：<code>{escape(name)}</code>")
        for name, err in route_report.errors.items():
            route_rows.append(f"· 错误：<code>{escape(name)}</code> · {escape(err)}")
        if not route_rows:
            route_rows.append("· <i>本次没有新增自动收纳动作。</i>")

        active_rows = [f"· <b>{escape(name)}</b> · <code>{cnt}</code> 个群" for name, cnt in sync_report.active.items()]
        if not active_rows:
            active_rows = ["· <i>当前没有读取到任何分组群数据。</i>"]

        message = panel(title, [section("执行摘要", [bullet("结果", status), bullet("耗时", f"{sync_report.elapsed_seconds:.1f} 秒"), bullet("时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))]), section("分组变动", folder_rows), section("自动收纳", route_rows), section("当前规模", active_rows[:8])], f"<i>发现新分组后，发送 <code>{escape(self.config.cmd_prefix)}enable 分组名</code> 即可开启监听。</i>")
        try:
            await self.client.send_message(target, message, link_preview=False)
        except Exception as exc:
            self.logger.warning("send sync report failed: %s", exc)

    async def send_startup_notification(self) -> None:
        assert self.client is not None
        rows = self.db.list_folders()
        enabled = [row for row in rows if int(row["enabled"]) == 1]
        folder_rows = []
        for row in enabled[:8]:
            folder_name = row["folder_name"]
            folder_rows.append(f"· <b>{escape(folder_name)}</b> · 群 <code>{self.db.count_cache_for_folder(folder_name)}</code> · 规则 <code>{self.db.count_rules_for_folder(folder_name)}</code>")
        if not folder_rows:
            folder_rows = ["· <i>当前没有开启任何分组监听。</i>"]

        route_rows = [f"· <code>{escape(row['pattern'])}</code> → <code>{escape(row['folder_name'])}</code>" for row in self.db.list_routes()[:8]] or ["· <i>当前没有自动收纳规则。</i>"]
        target_map, valid_rules = self.db.build_target_map(self.config.global_alert_channel_id)
        startup_card = panel("TG-Radar 已上线", [section("运行概况", [bullet("架构", "Plan C / Admin + Core / SQLite WAL"), bullet("版本", __version__), bullet("启动时间", self.started_at.strftime("%Y-%m-%d %H:%M:%S")), bullet("自动同步", f"每日 {self.config.auto_sync_time}" if self.config.auto_sync_enabled else "已关闭", code=False), bullet("热更新", "事件驱动 / 变更即触发")]), section("当前规模", [bullet("活跃分组", f"{len(enabled)} / {len(rows)}"), bullet("监听目标", f"{len(target_map)} 个群 / 频道"), bullet("生效规则", f"{valid_rules} 条")]), section("已启用分组", folder_rows), section("自动收纳规则", route_rows)], f"<i>在收藏夹发送 <code>{escape(self.config.cmd_prefix)}help</code> 可以打开完整管理面板。</i>")

        last_msg_path = self.config.work_dir / ".last_msg"
        target = self.config.notify_channel_id if self.config.notify_channel_id is not None else "me"
        msg_obj = None
        if last_msg_path.exists():
            try:
                ctx = json.loads(last_msg_path.read_text(encoding="utf-8"))
                action = ctx.get("action", "restart")
                prefix_text = "✨ <b>代码更新完成</b>\n\n" if action == "update" else "🔄 <b>服务重启完成</b>\n\n"
                msg_obj = await self.client.edit_message("me", int(ctx["msg_id"]), prefix_text + startup_card)
                last_msg_path.unlink(missing_ok=True)
                self.db.log_event("INFO", "RESTORE", f"system back online after {action}")
            except Exception:
                pass

        if msg_obj is None:
            try:
                msg_obj = await self.client.send_message(target, startup_card, link_preview=False)
            except Exception as exc:
                self.logger.warning("startup notification failed: %s", exc)

        # 系统通知默认保留，不自动回收。

    async def delete_later(self, msg, delay: int) -> None:
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass

    async def safe_reply(self, event: events.NewMessage.Event, text: str, auto_delete: int | None = None, prefer_edit: bool = True, recycle_source_on_reply: bool = True) -> None:
        delete_after = self.config.panel_auto_delete_seconds if auto_delete is None else auto_delete
        msg = None
        edited_original = False

        if prefer_edit:
            try:
                if getattr(event, "out", False):
                    msg = await event.edit(text)
                    edited_original = True
            except Exception:
                msg = None

        if msg is None:
            try:
                msg = await event.reply(text)
            except Exception as exc:
                self.logger.warning("safe_reply failed: %s", exc)
                self.db.log_event("ERROR", "COMMAND", f"safe_reply failed: {exc}")
                return

        if msg and delete_after > 0:
            asyncio.create_task(self.delete_later(msg, delete_after))

        if msg is not None and not edited_original and recycle_source_on_reply and self.config.recycle_fallback_command_seconds > 0:
            try:
                asyncio.create_task(self.delete_later(event.message, self.config.recycle_fallback_command_seconds))
            except Exception:
                pass

    async def run_route_scan_command(self, event: events.NewMessage.Event) -> None:
        result = self.command_bus.submit(
            "route_scan",
            payload={"reply_to": int(event.id)},
            priority=12,
            dedupe_key="route_scan",
            origin="telegram",
            visible=True,
            delay_seconds=self.config.manual_heavy_delay_seconds + 2,
        )
        self.db.log_event("INFO", "JOB_QUEUE", "manual route_scan queued")
        title = "自动收纳扫描已排队" if result.created else "自动收纳扫描已在后台等待"
        await self.safe_reply(event, panel(title, [section("调度说明", ["· 前台已立即返回，不会阻塞其它命令", "· 系统会在空闲时段启动扫描", "· 若命中目标，会自动排入补群队列"])]), auto_delete=0)

    def render_jobs_message(self) -> str:
        rows = self.db.list_open_jobs(20)
        if not rows:
            return panel("后台任务队列", [section("当前状态", ["· <i>当前没有排队或执行中的后台任务。</i>"])])
        blocks = []
        for row in rows:
            when = row["run_after"] or "立即"
            blocks.append(
                f"<b>{escape(row['kind'])}</b>\n"
                f"· 状态：<code>{escape(row['status'])}</code>\n"
                f"· 优先级：<code>{row['priority']}</code>\n"
                f"· 计划执行：<code>{escape(when)}</code>\n"
                f"· 更新时间：<code>{escape(row['updated_at'])}</code>"
            )
        return panel("后台任务队列", [section("排队 / 执行中", blocks)], "<i>重任务会延迟、合并、串行执行，以保证前台命令响应优先。</i>")

    def render_help_message(self) -> str:
        prefix = escape(self.config.cmd_prefix)
        return panel(
            "TG-Radar 管理面板",
            [
                section("快速入口", [
                    f"<code>{prefix}help</code> · 打开帮助面板",
                    f"<code>{prefix}status</code> · 查看系统状态",
                    f"<code>{prefix}ping</code> · 快速检测是否在线",
                    f"<code>{prefix}log</code> · 查看关键事件",
                ]),
                section("分组与规则", [
                    f"<code>{prefix}folders</code> · 查看全部 TG 分组",
                    f"<code>{prefix}rules 分组名</code> · 查看分组规则",
                    f"<code>{prefix}enable 分组名</code> · 开启监控",
                    f"<code>{prefix}disable 分组名</code> · 关闭监控",
                    f"<code>{prefix}addrule 分组 规则名 关键词...</code> · 追加新词到同名规则",
                    f"<code>{prefix}setrule 分组 规则名 表达式</code> · 直接覆盖整条规则",
                    f"<code>{prefix}delrule 分组 规则名</code> · 删除整条规则",
                    f"<code>{prefix}delrule 分组 规则名 关键词...</code> · 仅删除规则里的部分词",
                ]),
                section("自动收纳", [
                    f"<code>{prefix}routes</code> · 查看自动收纳规则",
                    f"<code>{prefix}addroute 分组 关键词...</code> · 新增收纳规则",
                    f"<code>{prefix}delroute 分组</code> · 删除收纳规则",
                ]),
                section("系统维护", [
                    f"<code>{prefix}sync</code> · 手动执行一次同步",
                    f"<code>{prefix}config</code> · 查看关键配置",
                    f"<code>{prefix}version</code> · 查看版本信息",
                    f"<code>{prefix}setnotify ID/off</code> · 设置系统通知目标",
                    f"<code>{prefix}setalert ID/off</code> · 设置默认告警目标",
                    f"<code>{prefix}setprefix 新前缀</code> · 修改命令前缀",
                    f"<code>{prefix}routescan</code> · 手动执行一次自动收纳扫描",
                    f"<code>{prefix}jobs</code> · 查看后台任务队列",
                    f"<code>{prefix}update</code> · 拉取代码并重启",
                    f"<code>{prefix}restart</code> · 直接重启双服务",
                ]),
                section("规则输入说明", [
                    "· 空格、英文逗号、中文逗号都会被识别为分隔符",
                    "· 单个正则表达式会按原样使用",
                    "· 同名规则默认追加，不会直接覆盖旧词",
                    "· 分组名、规则名、短语关键词如包含空格，请用引号包起来",
                ]),
                section("消息策略", [
                    "· 关键词告警默认长期保留，不自动回收",
                    "· 系统通知默认保留，不自动回收",
                    f"· 临时面板可在 {self.config.panel_auto_delete_seconds} 秒后自动回收",
                ]),
            ],
            "<i>发送命令后，系统会优先编辑原命令消息；耗时任务会先回执，再在后台继续执行。规则与分组变更会直接通知 Core 重载。</i>",
        )

    def render_config_message(self) -> str:
        notify_target = self.config.notify_channel_id if self.config.notify_channel_id is not None else "Saved Messages"
        alert_target = self.config.global_alert_channel_id if self.config.global_alert_channel_id is not None else "未设置"
        return panel(
            "TG-Radar 关键配置",
            [
                section("通信与路由", [
                    bullet("API_ID", self.config.api_id),
                    bullet("默认告警", alert_target),
                    bullet("系统通知", notify_target),
                    bullet("命令前缀", self.config.cmd_prefix),
                ]),
                section("运行策略", [
                    bullet("运行模式", self.config.operation_mode),
                    bullet("自动同步", f"每日 {self.config.auto_sync_time}" if self.config.auto_sync_enabled else "已关闭", code=False),
                    bullet("自动收纳", f"每日 {self.config.auto_route_time}" if self.config.auto_route_enabled else "已关闭", code=False),
                    bullet("热更新", "事件驱动"),
                    bullet("临时面板回收", f"{self.config.panel_auto_delete_seconds} 秒"),
                    bullet("系统通知保留", "默认保留，不自动回收", code=False),
                    bullet("命令回执策略", "优先编辑原消息，重任务延迟排队", code=False),
                ]),
                section("部署信息", [
                    bullet("服务前缀", self.config.service_name_prefix),
                    bullet("终端命令", "TR"),
                    bullet("工作目录", shorten_path(self.config.work_dir), code=False),
                    bullet("仓库地址", self.config.repo_url or "未设置", code=False),
                ]),
            ],
        )

    def render_status_message(self) -> str:
        stats = self.db.get_runtime_stats()
        last_folder = stats.get("last_hit_folder") or "暂无记录"
        last_time = stats.get("last_hit_time") or "暂无记录"
        rows = self.db.list_folders()
        enabled_cnt = sum(1 for row in rows if int(row["enabled"]) == 1)
        target_map, valid_rules = self.db.build_target_map(self.config.global_alert_channel_id)
        queue_size = self.db.pending_route_count()
        active_rows = []
        for row in rows:
            if int(row["enabled"]) != 1:
                continue
            folder_name = row["folder_name"]
            active_rows.append(f"· {escape(folder_name)} · 群 <code>{self.db.count_cache_for_folder(folder_name)}</code> · 规则 <code>{self.db.count_rules_for_folder(folder_name)}</code>")
            if len(active_rows) >= 8:
                break
        if not active_rows:
            active_rows = ["· <i>暂无启用分组。</i>"]
        q_info = f"{queue_size} 个任务待处理" if queue_size > 0 else "空闲"
        return panel("TG-Radar 详细状态", [section("运行状态", [bullet("系统状态", "稳定运行中", code=False), bullet("持续运行", format_duration((datetime.now() - self.started_at).total_seconds())), bullet("运行模式", self.config.operation_mode), bullet("自动同步", f"每日 {self.config.auto_sync_time}" if self.config.auto_sync_enabled else "已关闭", code=False), bullet("自动收纳", f"每日 {self.config.auto_route_time}" if self.config.auto_route_enabled else "已关闭", code=False), bullet("热更新", "事件驱动"), bullet("路由队列", q_info, code=False), bullet("交互策略", "优先回执命令，再延迟执行重任务", code=False)]), section("监控规模", [bullet("活跃分组", f"{enabled_cnt} / {len(rows)}"), bullet("监听目标", f"{len(target_map)} 个群 / 频道"), bullet("生效规则", f"{valid_rules} 条"), bullet("自动收纳规则", f"{len(self.db.list_routes())} 条")]), section("历史统计", [bullet("总计命中", stats.get("total_hits", "0")), bullet("最近命中分组", last_folder), bullet("最近命中时间", last_time)]), section("已启用分组", active_rows)])

    def render_sync_message(self, sync_report: SyncReport, route_report: RouteReport) -> str:
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
            route_rows = ["· <i>没有新的自动收纳动作。</i>"]

        return panel("TG-Radar 同步完成", [section("同步结果", [bullet("变动状态", "发现变动并已更新" if sync_report.has_changes else "数据无变动", code=False), bullet("耗时", f"{sync_report.elapsed_seconds:.1f} 秒"), bullet("新分组", len(sync_report.discovered)), bullet("改名", len(sync_report.renamed)), bullet("删除", len(sync_report.deleted))]), section("分组变动", folder_rows), section("自动收纳", route_rows)], f"<i>如果发现了新分组，记得发送 <code>{escape(self.config.cmd_prefix)}enable 分组名</code> 开启监控。</i>")

    def spawn_task(self, coro) -> None:
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
        cmd = ["bash", "-lc", f"sleep {delay}; systemctl restart {self.config.service_name_prefix}-core {self.config.service_name_prefix}-admin"]
        subprocess.Popen(cmd)

    def write_last_message(self, msg_id: int, action: str) -> None:
        path = self.config.work_dir / ".last_msg"
        path.write_text(json.dumps({"chat_id": "me", "msg_id": msg_id, "action": action}, ensure_ascii=False), encoding="utf-8")


async def run(work_dir: Path) -> None:
    app = AdminApp(work_dir)
    await app.run()
