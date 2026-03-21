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
from .telegram_utils import blockquote_preview, bullet, dialog_filter_title, escape, format_duration, html_code, panel, section, shorten_path, soft_kv
from .version import __version__


class AdminApp:
    def __init__(self, work_dir: Path) -> None:
        self.config = load_config(work_dir)
        self.logger = setup_logger("tg-radar-admin", self.config.logs_dir / "admin.log")
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

    def _notify_scheduler(self) -> None:
        if self.scheduler:
            self.scheduler.notify_new_job()

    # ── Session 自愈 ──

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
                self.logger.warning("session 损坏，正在恢复")
                session.unlink(missing_ok=True)
        core = self.config.core_session.with_suffix(".session")
        if core.exists():
            try:
                shutil.copy2(core, session)
                self.logger.info("已从 core session 恢复")
                return session
            except Exception:
                pass
        raise FileNotFoundError("缺少 session 文件，请执行 TR reauth")

    # ── 主循环 ──

    async def run(self) -> None:
        self.config.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_session()
        lock_file = self.config.work_dir / ".admin.lock"
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
        try:
            if os.name != "nt":
                import fcntl
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception as exc:
            raise RuntimeError("Admin 进程已在运行中") from exc

        async with TelegramClient(str(self.config.session_path), self.config.api_id, self.config.api_hash) as client:
            self.client = client
            client.parse_mode = "html"
            me = await client.get_me()
            self.self_id = int(getattr(me, "id", 0) or 0)
            self.logger.info("已登录 @%s (ID: %s)", getattr(me, "username", "?"), self.self_id)
            self.plugin_manager.load_admin_plugins()
            self.plugin_manager.load_core_plugins()
            await self.plugin_manager.run_healthchecks()
            self._register_handler(client)
            self.db.log_event("INFO", "ADMIN", f"Admin 已启动 v{__version__}")
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
            tasks = [asyncio.create_task(self.scheduler.run()), asyncio.create_task(client.run_until_disconnected()), asyncio.create_task(self.stop_event.wait())]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            self.stop_event.set()
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            self.db.log_event("INFO", "ADMIN", "Admin 正在关闭")

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
            self._startup_sync_note = f"新增 {len(sr.discovered)} 个分组，缓存 {sum(sr.active.values())} 个目标。"
        except Exception as exc:
            self._startup_sync_note = f"校准失败: {exc}"
            self.db.log_event("ERROR", "SYNC", str(exc))

    # ── 命令分发 ──

    def _register_handler(self, client: TelegramClient) -> None:
        @client.on(events.NewMessage(outgoing=True, incoming=True))
        async def on_message(event) -> None:
            if not event.is_private:
                return
            if int(getattr(event, "chat_id", 0) or 0) != self.self_id:
                return
            text = (event.raw_text or "").strip()
            if not text or not text.startswith(self.config.cmd_prefix):
                self.spawn_task(self.plugin_manager.process_core_message(self, event))
                return
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
            async def _runner():
                try:
                    ok = await self.plugin_manager.dispatch_admin_command(command, self, event, args)
                    if not ok:
                        await self.safe_reply(event, panel("TG-Radar · 未知命令", [section("提示", [f"发送 <code>{escape(self.config.cmd_prefix)}help</code> 查看命令列表"])]))
                except Exception as exc:
                    self.logger.exception("命令异常: %s", exc)
                    self.db.log_event("ERROR", "COMMAND", f"{trace} {exc}")
                    await self.safe_reply(event, panel("TG-Radar · 命令异常", [section("错误", [blockquote_preview(str(exc), 500)]), section("跟踪", [bullet("ID", trace, code=False)])]), auto_delete=0)
            self.spawn_task(_runner())

    # ── 消息工具 ──

    async def safe_reply(self, event, text: str, auto_delete: int | None = None, prefer_edit: bool = True) -> None:
        # 从 general 插件配置读取超时参数
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
        await self.safe_reply(event, panel("TG-Radar · 同步" + ("已接收" if r.created else "排队中"), [section("状态", ["已进入后台调度，完成后回写至此消息。"])]), auto_delete=0)

    async def run_update_command(self, event) -> None:
        if not (self.config.work_dir / ".git").exists():
            await self.safe_reply(event, panel("TG-Radar · 更新", [section("提示", ["当前目录不是 Git 仓库。"])]), auto_delete=0)
            return
        self.command_bus.submit("update_repo", payload={"reply_to": int(event.id), "trace": self._event_trace(event)}, priority=15, dedupe_key="update_repo", origin="telegram", visible=True, delay_seconds=self.config.update_delay_seconds)
        await self.safe_reply(event, panel("TG-Radar · 更新已接收", [section("状态", ["正在拉取核心与插件仓库，完成后回写。"])]), auto_delete=0)

    async def run_route_scan_command(self, event) -> None:
        self.command_bus.submit("route_scan", payload={"reply_to": int(event.id), "trace": self._event_trace(event)}, priority=12, dedupe_key="route_scan", origin="telegram", visible=True, delay_seconds=self.config.manual_heavy_delay_seconds + 2)
        await self.safe_reply(event, panel("TG-Radar · 归纳扫描已接收", [section("状态", ["完成后回写至此消息。"])]), auto_delete=0)

    def queue_snapshot_flush(self) -> None:
        now = time.monotonic()
        if now - self._last_snapshot_queued_at < self.config.snapshot_flush_debounce_seconds:
            return
        self._last_snapshot_queued_at = now
        self.command_bus.submit("config_snapshot_flush", priority=220, dedupe_key="config_snapshot_flush", origin="system", visible=False, delay_seconds=self.config.snapshot_flush_debounce_seconds)

    def queue_core_reload(self, reason: str, detail: str = "") -> None:
        self.command_bus.submit("reload_core", payload={"reason": reason, "detail": detail}, priority=40, dedupe_key="reload_core", origin="system", visible=False, delay_seconds=self.config.reload_debounce_seconds)

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

            # Parse git output into clean summary
            raw = result.detail or ""
            pull_rows = []
            for line in raw.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("[core]"):
                    txt = line.replace("[core]", "").strip()
                    if "Already up to date" in txt or "already up to date" in txt.lower():
                        pull_rows.append(bullet("核心仓库", "已是最新", code=False))
                    elif "Updating" in txt or "file changed" in txt or "files changed" in txt:
                        pull_rows.append(bullet("核心仓库", "已更新", code=False))
                    else:
                        pull_rows.append(bullet("核心仓库", txt, code=False))
                elif line.startswith("[plugins]"):
                    txt = line.replace("[plugins]", "").strip()
                    if "Already up to date" in txt or "already up to date" in txt.lower():
                        pull_rows.append(bullet("插件仓库", "已是最新", code=False))
                    elif "file changed" in txt or "files changed" in txt:
                        pull_rows.append(bullet("插件仓库", "已更新", code=False))
                    else:
                        pull_rows.append(bullet("插件仓库", txt, code=False))
            if not pull_rows:
                pull_rows.append(bullet("结果", "已是最新", code=False))

            secs = [section("仓库状态", pull_rows)]
            if reload_results:
                secs.append(section(f"自动重载 · {len(changed)} 个插件", reload_results))
            elif ok and not changed:
                secs.append(section("插件", ["无文件变更，无需重载。"]))

            footer = None
            if ok and not changed:
                footer = f"<i>如有核心代码变更，请执行 <code>{escape(self.config.cmd_prefix)}restart</code></i>"

            if rt:
                await self.edit_message_by_id(rt, panel("TG-Radar · 更新" + ("完成" if ok else "失败"), secs, footer))
        elif job.kind == "restart_services" and rt:
            await self.edit_message_by_id(rt, panel("TG-Radar · 即将重启", [section("说明", ["重启指令已下发，服务会自动拉起。"])]))

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
            section("仓库", [bullet("核心", c.repo_url or "未设置", code=False), bullet("插件", c.plugins_repo_url or "未设置", code=False)]),
        ])

    def render_status_message(self) -> str:
        stats = self.db.get_runtime_stats()
        rows = self.db.list_folders()
        enabled = sum(1 for r in rows if int(r["enabled"]))
        tm, vr = self.db.build_target_map(self.config.global_alert_channel_id)
        cc, rc = self.db.count_cache_all_folders(), self.db.count_rules_all_folders()
        active = []
        for r in rows:
            if not int(r["enabled"]):
                continue
            fn = r["folder_name"]
            active.append(f"· {escape(fn)}  群 <code>{cc.get(fn, 0)}</code>  规则 <code>{rc.get(fn, 0)}</code>")
            if len(active) >= 8:
                break
        return panel("TG-Radar · 系统状态", [
            section("运行", [bullet("运行时间", format_duration((datetime.now() - self.started_at).total_seconds())), bullet("模式", self.config.operation_mode), bullet("待处理队列", self.db.pending_route_count())]),
            section("监控", [bullet("分组", f"{enabled} / {len(rows)}"), bullet("监听目标", len(tm)), bullet("生效规则", vr)]),
            section("命中统计", [bullet("总计", stats.get("total_hits", "0")), bullet("最近分组", stats.get("last_hit_folder") or "—"), bullet("最近时间", stats.get("last_hit_time") or "—")]),
            section("已启用分组", active or ["<i>暂无</i>"]),
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
        tm, vr = self.db.build_target_map(self.config.global_alert_channel_id)
        enabled = [r for r in rows if int(r["enabled"])]
        al = sum(1 for p in self.plugin_manager.list_plugins("admin") if p.loaded)
        cl = sum(1 for p in self.plugin_manager.list_plugins("core") if p.loaded)
        errs = sum(1 for p in self.plugin_manager.list_plugins() if p.load_error)
        fb = []
        for r in rows[:12]:
            fn = r["folder_name"]
            st = "开启" if int(r["enabled"]) else "关闭"
            fb.append(f"· {escape(fn)}  {st}  群 <code>{cc.get(fn, 0)}</code>  规则 <code>{rc.get(fn, 0)}</code>")
        text = panel("TG-Radar · 启动完成", [
            section("系统", [bullet("版本", __version__), bullet("模式", self.config.operation_mode), bullet("前缀", self.config.cmd_prefix)]),
            section("监控", [bullet("分组", f"{len(enabled)} / {len(rows)}"), bullet("缓存", sum(cc.values())), bullet("目标", len(tm)), bullet("规则", vr)]),
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
        subprocess.Popen(["bash", "-lc", f"sleep {delay}; systemctl restart {n}-core {n}-admin"])

    def write_last_message(self, msg_id: int, action: str) -> None:
        (self.config.work_dir / ".last_msg").write_text(json.dumps({"chat_id": "me", "msg_id": msg_id, "action": action}), encoding="utf-8")

    def _event_trace(self, event) -> str:
        return str(getattr(event, "_tgr_trace", "cmd-?"))


async def run(work_dir: Path) -> None:
    await AdminApp(work_dir).run()
