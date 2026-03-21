from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import load_config, sync_snapshot_to_config
from .db import AdminJob, RadarDB
from .sync_logic import RouteReport, SyncReport, scan_auto_routes, sync_dialog_folders


@dataclass
class JobResult:
    status: str
    summary: str
    detail: str = ""
    payload: dict[str, Any] | None = None
    notify: bool = False
    log_action: str | None = None
    log_level: str = "INFO"


class AdminExecutors:
    def __init__(self, app: Any) -> None:
        self.app = app

    @property
    def db(self) -> RadarDB:
        return self.app.db

    @property
    def config(self):
        return self.app.config

    async def execute(self, job: AdminJob) -> JobResult:
        kind = job.kind
        dispatch = {
            "sync_manual": lambda: self._sync(job, False),
            "sync_auto": lambda: self._sync(job, True),
            "route_apply": lambda: self._route_apply(job),
            "route_scan": lambda: self._route_scan(job),
            "update_repo": lambda: self._update(job),
            "restart_services": lambda: self._restart(job),
            "config_snapshot_flush": lambda: self._snapshot_flush(job),
            "reload_core": lambda: self._reload_core(job),
        }
        handler = dispatch.get(kind)
        if handler:
            return await handler()
        return JobResult(status="done", summary=f"未实现: {kind}", log_action="COMMAND", log_level="ERROR")

    async def _sync(self, job: AdminJob, automatic: bool) -> JobResult:
        sr = await sync_dialog_folders(self.app.client, self.db, self.config)
        rr = await scan_auto_routes(self.app.client, self.db, self.config)
        self.app.last_sync_result = (sr, rr)
        if rr.queued:
            self.app.command_bus.submit("route_apply", priority=55, dedupe_key="route_apply", origin="system", visible=False, delay_seconds=self.config.route_apply_delay_seconds)
        has_changes = sr.has_changes or rr.created or rr.queued
        if has_changes:
            self.app.command_bus.submit("config_snapshot_flush", priority=200, dedupe_key="config_snapshot_flush", origin="system", visible=False, delay_seconds=self.config.snapshot_flush_debounce_seconds)
            self.app.command_bus.submit("reload_core", payload={"reason": "sync"}, priority=40, dedupe_key="reload_core", origin="system", visible=False, delay_seconds=self.config.reload_debounce_seconds)
        detail = f"changed={sr.has_changes}; queued={sum(rr.queued.values())}; created={len(rr.created)}"
        return JobResult(status="done", summary="有变动" if has_changes else "无变动", detail=detail, payload={"sync_report": sr, "route_report": rr}, notify=automatic and has_changes, log_action="AUTO_SYNC" if automatic else "SYNC")

    async def _route_scan(self, job: AdminJob) -> JobResult:
        rr = await scan_auto_routes(self.app.client, self.db, self.config)
        if rr.queued:
            self.app.command_bus.submit("route_apply", priority=55, dedupe_key="route_apply", origin="system", visible=False, delay_seconds=self.config.route_apply_delay_seconds)
        if rr.created or rr.queued:
            self.app.command_bus.submit("config_snapshot_flush", priority=200, dedupe_key="config_snapshot_flush", origin="system", visible=False, delay_seconds=self.config.snapshot_flush_debounce_seconds)
        return JobResult(status="done", summary="扫描完成", detail=f"queued={sum(rr.queued.values())}", payload={"route_report": rr}, log_action="ROUTE_TASK")

    async def _route_apply(self, job: AdminJob) -> JobResult:
        applied = 0
        while True:
            task = self.db.get_next_route_task()
            if task is None:
                break
            try:
                await self.app.apply_route_task(task)
                self.db.complete_route_task(task.id)
                applied += 1
                await asyncio.sleep(0.5)
            except Exception as exc:
                self.db.fail_route_task(task.id, str(exc), retry=task.retries < 3)
                self.db.log_event("ERROR", "ROUTE_TASK", f"{task.folder_name}: {exc}")
        if applied:
            self.app.command_bus.submit("config_snapshot_flush", priority=200, dedupe_key="config_snapshot_flush", origin="system", visible=False, delay_seconds=self.config.snapshot_flush_debounce_seconds)
        return JobResult(status="done", summary=f"应用 {applied} 个任务", detail=f"applied={applied}", log_action="ROUTE_TASK")

    async def _run_git_pull(self, repo_dir: Path) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec("git", "-C", str(repo_dir), "pull", "--ff-only", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        stdout, _ = await proc.communicate()
        return proc.returncode, (stdout or b"").decode("utf-8", errors="replace").strip()

    async def _update(self, job: AdminJob) -> JobResult:
        # Snapshot plugin mtimes BEFORE pull
        before = self._snapshot_plugin_mtimes()

        outputs = []
        code, out = await self._run_git_pull(self.config.work_dir)
        outputs.append(f"[core] {out or 'ok'}")
        if code != 0:
            return JobResult(status="failed", summary="核心更新失败", detail="\n".join(outputs), log_action="UPDATE", log_level="ERROR")
        proot = self.config.plugins_root.parent
        if (proot / ".git").exists():
            pc, po = await self._run_git_pull(proot)
            outputs.append(f"[plugins] {po or 'ok'}")
            if pc != 0:
                return JobResult(status="failed", summary="插件更新失败", detail="\n".join(outputs), log_action="UPDATE", log_level="ERROR")

        # Compare AFTER pull — find changed plugins
        after = self._snapshot_plugin_mtimes()
        changed = sorted(set(
            [n for n, t in after.items() if before.get(n) != t] +
            [n for n in after if n not in before]
        ))

        return JobResult(status="done", summary="更新完成", detail="\n".join(outputs),
                         payload={"changed_plugins": changed}, log_action="UPDATE")

    def _snapshot_plugin_mtimes(self) -> dict[str, float]:
        from pathlib import Path
        result = {}
        root = Path(self.config.plugins_root)
        if not root.exists():
            return result
        for f in root.rglob("*.py"):
            if f.name != "__init__.py":
                result[f.stem] = f.stat().st_mtime
        return result

    async def _restart(self, job: AdminJob) -> JobResult:
        delay = float(job.payload.get("delay", self.config.restart_delay_seconds))
        self.app.restart_services(delay=delay)
        return JobResult(status="done", summary="重启已下发", detail=f"delay={delay}", log_action="RESTART")

    async def _reload_core(self, job: AdminJob) -> JobResult:
        reason = str(job.payload.get("reason", "runtime_change"))
        svc = self.config.service_name_prefix
        proc = await asyncio.create_subprocess_exec("systemctl", "kill", "-s", "USR1", f"{svc}-core", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return JobResult(status="done", summary="Core 已重载", detail=reason, log_action="CORE_RELOAD")
        pid_file = self.config.runtime_dir / "core.pid"
        if pid_file.exists():
            try:
                import os, signal
                os.kill(int(pid_file.read_text().strip()), signal.SIGUSR1)
                return JobResult(status="done", summary="Core 已重载 (PID)", detail=reason, log_action="CORE_RELOAD")
            except Exception:
                pass
        return JobResult(status="failed", summary="Core 重载失败", log_action="CORE_RELOAD", log_level="ERROR")

    async def _snapshot_flush(self, job: AdminJob) -> JobResult:
        self.app.config = load_config(self.config.work_dir)
        sync_snapshot_to_config(self.config.work_dir, self.db)
        return JobResult(status="done", summary="快照已刷新")
