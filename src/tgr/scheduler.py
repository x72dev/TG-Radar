from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .db import AdminJob
from .executors import AdminExecutors


class AdminScheduler:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.executors = AdminExecutors(app)
        self.stop_event = app.stop_event
        self.running_tasks: set[asyncio.Task] = set()
        self.wakeup = asyncio.Event()
        self.aps: AsyncIOScheduler | None = None

    @property
    def db(self):
        return self.app.db

    @property
    def config(self):
        return self.app.config

    def _plugin_cfg(self, plugin_name: str, key: str, default: Any) -> Any:
        """Read a value from a plugin's config file."""
        cfg_file = self.app.plugin_manager.get_plugin_config_file(plugin_name, {})
        return cfg_file.get(key, default)

    async def run(self) -> None:
        self.aps = AsyncIOScheduler()
        self._install_daily_jobs()
        self.aps.start()
        tasks = [asyncio.create_task(self._dispatcher_loop()), asyncio.create_task(self._housekeeping_loop())]
        await self.stop_event.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.aps:
            try:
                self.aps.shutdown(wait=False)
            except Exception:
                pass
        if self.running_tasks:
            await asyncio.gather(*list(self.running_tasks), return_exceptions=True)

    def _install_daily_jobs(self) -> None:
        if not self.aps:
            return
        # Read from routes plugin config (or defaults)
        sync_on = self._plugin_cfg("routes", "auto_sync_enabled", True)
        sync_t = str(self._plugin_cfg("routes", "auto_sync_time", "03:40"))
        route_on = self._plugin_cfg("routes", "auto_route_enabled", True)
        route_t = str(self._plugin_cfg("routes", "auto_route_time", "04:20"))

        if sync_on:
            h, m = self._parse_hm(sync_t, 3, 40)
            self.aps.add_job(self._queue_daily, CronTrigger(hour=h, minute=m), kwargs={"kind": "sync_auto", "desc": "自动同步", "pri": 90, "dk": "sync_auto"}, id="daily_sync", replace_existing=True, coalesce=True, max_instances=1, misfire_grace_time=3600)
        if route_on:
            h, m = self._parse_hm(route_t, 4, 20)
            self.aps.add_job(self._queue_daily, CronTrigger(hour=h, minute=m), kwargs={"kind": "route_scan", "desc": "自动归纳", "pri": 95, "dk": "route_scan"}, id="daily_route", replace_existing=True, coalesce=True, max_instances=1, misfire_grace_time=3600)

    @staticmethod
    def _parse_hm(t: str, dh: int, dm: int) -> tuple[int, int]:
        parts = t.split(":", 1)
        try:
            return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        except Exception:
            return dh, dm

    async def _queue_daily(self, *, kind: str, desc: str, pri: int, dk: str) -> None:
        jitter = 0
        if self.config.daily_jitter_minutes > 0:
            jitter += random.randint(0, self.config.daily_jitter_minutes * 60)
        recent = time.monotonic() - self.app.last_command_ts
        if recent < self.config.idle_grace_seconds:
            jitter += int(self.config.idle_grace_seconds - recent)
        busy = sum(self.db.count_open_jobs(k) for k in ("sync_manual", "sync_auto", "route_scan", "route_apply", "update_repo", "restart_services"))
        if busy > 0:
            jitter += 180
        r = self.app.command_bus.submit(kind, priority=pri, dedupe_key=dk, origin="scheduler", visible=False, delay_seconds=jitter)
        if r.created:
            self.db.log_event("INFO", "JOB_QUEUE", f"{desc} 延迟 {jitter}s")

    def notify_new_job(self) -> None:
        self.wakeup.set()

    async def _dispatcher_loop(self) -> None:
        while not self.stop_event.is_set():
            if len(self.running_tasks) >= self.config.max_parallel_admin_jobs:
                await self._wait(self.config.scheduler_poll_seconds)
                continue
            job = self.db.claim_next_job()
            if job is None:
                await self._wait(self.config.scheduler_poll_seconds)
                continue
            task = asyncio.create_task(self._run_job(job))
            self.running_tasks.add(task)
            task.add_done_callback(self.running_tasks.discard)

    async def _run_job(self, job: AdminJob) -> None:
        self.db.log_event("INFO", "JOB_START", f"{job.kind}#{job.id}")
        try:
            result = await self.executors.execute(job)
        except Exception as exc:
            self.db.fail_job(job.id, str(exc))
            self.db.log_event("ERROR", "JOB_FAIL", f"{job.kind}#{job.id}: {exc}")
            await self.app.notify_job_failure(job, exc)
            self.notify_new_job()
            return
        if result.log_action:
            self.db.log_event(result.log_level, result.log_action, result.detail or result.summary)
        self.db.finish_job(job.id)
        self.db.log_event("INFO", "JOB_DONE", f"{job.kind}#{job.id}: {result.summary}")
        await self.app.after_job(job, result)
        self.notify_new_job()

    async def _wait(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self.wakeup.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        self.wakeup.clear()

    async def _housekeeping_loop(self) -> None:
        while not self.stop_event.is_set():
            self.db.cleanup_finished_jobs()
            await asyncio.sleep(120)
