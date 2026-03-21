from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig, load_config, sync_snapshot_to_config
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

    # FIX BUG-07: always access config through app to get latest reference
    @property
    def db(self) -> RadarDB:
        return self.app.db

    @property
    def config(self) -> AppConfig:
        return self.app.config

    async def execute(self, job: AdminJob) -> JobResult:
        kind = job.kind
        if kind in {"sync_manual", "sync_auto"}:
            return await self._execute_sync(job, automatic=(kind == "sync_auto"))
        if kind == "route_apply":
            return await self._execute_route_apply(job)
        if kind == "route_scan":
            return await self._execute_route_scan(job)
        if kind == "update_repo":
            return await self._execute_update(job)
        if kind == "restart_services":
            return await self._execute_restart(job)
        if kind == "config_snapshot_flush":
            return await self._execute_snapshot_flush(job)
        if kind == "reload_core":
            return await self._execute_reload_core(job)
        return JobResult(status="done", summary=f"未实现的任务类型：{kind}", detail=str(job.payload or {}), log_action="COMMAND", log_level="ERROR")

    async def _execute_sync(self, job: AdminJob, automatic: bool) -> JobResult:
        sync_report: SyncReport = await sync_dialog_folders(self.app.client, self.db, self.config)
        route_report: RouteReport = await scan_auto_routes(self.app.client, self.db, self.config)
        self.app.last_sync_result = (sync_report, route_report)
        if route_report.queued:
            self.app.command_bus.submit("route_apply", priority=55, dedupe_key="route_apply", origin="system", visible=False, delay_seconds=self.config.route_apply_delay_seconds)
        if sync_report.has_changes or route_report.created or route_report.queued:
            self.app.command_bus.submit("config_snapshot_flush", priority=200, dedupe_key="config_snapshot_flush", origin="system", visible=False, delay_seconds=self.config.snapshot_flush_debounce_seconds)
            self.app.command_bus.submit("reload_core", payload={"reason": "sync_changed", "detail": f"changed={sync_report.has_changes}; queued={sum(route_report.queued.values())}; created={len(route_report.created)}"}, priority=40, dedupe_key="reload_core", origin="system", visible=False, delay_seconds=self.config.reload_debounce_seconds)
        action = "AUTO_SYNC" if automatic else "SYNC"
        summary = "发现变动并已更新" if sync_report.has_changes or route_report.created or route_report.queued else "同步完成，数据无变动"
        detail = f"changed={sync_report.has_changes}; queued={sum(route_report.queued.values())}; created={len(route_report.created)}"
        return JobResult(status="done", summary=summary, detail=detail, payload={"sync_report": sync_report, "route_report": route_report, "automatic": automatic}, notify=automatic and (sync_report.has_changes or route_report.created or route_report.queued), log_action=action)

    async def _execute_route_scan(self, job: AdminJob) -> JobResult:
        route_report: RouteReport = await scan_auto_routes(self.app.client, self.db, self.config)
        if route_report.queued:
            self.app.command_bus.submit("route_apply", priority=55, dedupe_key="route_apply", origin="system", visible=False, delay_seconds=self.config.route_apply_delay_seconds)
        if route_report.created or route_report.queued:
            self.app.command_bus.submit("config_snapshot_flush", priority=200, dedupe_key="config_snapshot_flush", origin="system", visible=False, delay_seconds=self.config.snapshot_flush_debounce_seconds)
        return JobResult(status="done", summary="自动归纳扫描完成", detail=f"queued={sum(route_report.queued.values())}; created={len(route_report.created)}", payload={"route_report": route_report}, notify=False, log_action="ROUTE_TASK")

    async def _execute_route_apply(self, job: AdminJob) -> JobResult:
        applied = 0
        while True:
            task = self.db.get_next_route_task()
            if task is None:
                break
            try:
                await self.app.apply_route_task(task)
                self.db.complete_route_task(task.id)
                applied += 1
                # ROBUST-04: pause between route operations
                await asyncio.sleep(0.5)
            except Exception as exc:
                retry = task.retries < 3
                self.db.fail_route_task(task.id, str(exc), retry=retry)
                self.db.log_event("ERROR", "ROUTE_TASK", f"{task.folder_name}: {exc}")
        if applied:
            self.app.command_bus.submit("config_snapshot_flush", priority=200, dedupe_key="config_snapshot_flush", origin="system", visible=False, delay_seconds=self.config.snapshot_flush_debounce_seconds)
        return JobResult(status="done", summary=f"自动归纳处理完成，应用 {applied} 个任务", detail=f"applied={applied}", log_action="ROUTE_TASK")

    async def _run_git_pull(self, repo_dir: Path) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo_dir), "pull", "--ff-only",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        return proc.returncode, (stdout or b"").decode("utf-8", errors="replace").strip()

    async def _execute_update(self, job: AdminJob) -> JobResult:
        outputs: list[str] = []
        code, out = await self._run_git_pull(self.config.work_dir)
        outputs.append(f"[core]\n{out or 'git pull ok'}")
        if code != 0:
            return JobResult(status="failed", summary="核心仓库更新失败", detail="\n\n".join(outputs), log_action="UPDATE", log_level="ERROR")
        plugins_root = self.config.plugins_root.parent
        if (plugins_root / ".git").exists():
            p_code, p_out = await self._run_git_pull(plugins_root)
            outputs.append(f"[plugins]\n{p_out or 'git pull ok'}")
            if p_code != 0:
                return JobResult(status="failed", summary="插件仓库更新失败", detail="\n\n".join(outputs), log_action="UPDATE", log_level="ERROR")
        return JobResult(status="done", summary="核心仓库与插件仓库更新完成", detail="\n\n".join(outputs), log_action="UPDATE")

    async def _execute_restart(self, job: AdminJob) -> JobResult:
        delay = float(job.payload.get("delay", self.config.restart_delay_seconds))
        self.app.restart_services(delay=delay)
        return JobResult(status="done", summary="重启指令已下发", detail=f"delay={delay}", log_action="RESTART")

    async def _execute_reload_core(self, job: AdminJob) -> JobResult:
        reason = str(job.payload.get("reason") or "runtime_change")
        detail = str(job.payload.get("detail") or reason)
        service_name = self.config.service_name_prefix
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "kill", "-s", "USR1", f"{service_name}-core",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = (stdout or b"").decode("utf-8", errors="replace").strip()
        if proc.returncode == 0:
            return JobResult(status="done", summary="Core 已触发重载", detail=detail or output or reason, log_action="CORE_RELOAD")
        # FIX ROBUST-05: more precise fallback using pid file
        pid_file = self.config.runtime_dir / "core.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                import os, signal
                os.kill(pid, signal.SIGUSR1)
                return JobResult(status="done", summary="Core 已触发重载 (PID)", detail=detail or reason, log_action="CORE_RELOAD")
            except Exception:
                pass
        return JobResult(status="failed", summary="Core 重载失败", detail=output or "无法向 Core 发送重载信号", log_action="CORE_RELOAD", log_level="ERROR")

    async def _execute_snapshot_flush(self, job: AdminJob) -> JobResult:
        self.app.config = load_config(self.config.work_dir)
        sync_snapshot_to_config(self.config.work_dir, self.db)
        return JobResult(status="done", summary="配置快照已刷新")
