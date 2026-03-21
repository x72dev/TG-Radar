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

    # FIX BUG-07: always access through app
    @property
    def db(self):
        return self.app.db

    @property
    def config(self):
        return self.app.config

    async def run(self) -> None:
        self.aps = AsyncIOScheduler()
        self._install_daily_jobs()
        self.aps.start()
        tasks = [
            asyncio.create_task(self._dispatcher_loop()),
            asyncio.create_task(self._housekeeping_loop()),
        ]
        await self.stop_event.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.aps is not None:
            try:
                self.aps.shutdown(wait=False)
            except Exception:
                pass
        if self.running_tasks:
            await asyncio.gather(*list(self.running_tasks), return_exceptions=True)

    def _install_daily_jobs(self) -> None:
        if self.aps is None:
            return
        if self.config.auto_sync_enabled:
            hour, minute = [int(x) for x in self.config.auto_sync_time.split(":", 1)]
            self.aps.add_job(
                self._queue_daily_job,
                CronTrigger(hour=hour, minute=minute),
                kwargs={
                    "kind": "sync_auto",
                    "description": "自动同步",
                    "priority": 90,
                    "dedupe_key": "sync_auto",
                },
                id="daily_sync",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=3600,
            )
        if self.config.auto_route_enabled:
            hour, minute = [int(x) for x in self.config.auto_route_time.split(":", 1)]
            self.aps.add_job(
                self._queue_daily_job,
                CronTrigger(hour=hour, minute=minute),
                kwargs={
                    "kind": "route_scan",
                    "description": "自动收纳扫描",
                    "priority": 95,
                    "dedupe_key": "route_scan",
                },
                id="daily_route_scan",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=3600,
            )

    async def _queue_daily_job(self, *, kind: str, description: str, priority: int, dedupe_key: str) -> None:
        jitter_seconds = 0
        if self.config.daily_jitter_minutes > 0:
            jitter_seconds += random.randint(0, int(self.config.daily_jitter_minutes) * 60)

        recent_cmd = time.monotonic() - self.app.last_command_ts
        if recent_cmd < self.config.idle_grace_seconds:
            jitter_seconds += int(self.config.idle_grace_seconds - recent_cmd)

        heavy_busy = sum(
            self.db.count_open_jobs(kind_name)
            for kind_name in ("sync_manual", "sync_auto", "route_scan", "route_apply", "update_repo", "restart_services")
        )
        if heavy_busy > 0:
            jitter_seconds += 180

        result = self.app.command_bus.submit(
            kind,
            priority=priority,
            dedupe_key=dedupe_key,
            origin="scheduler",
            visible=False,
            delay_seconds=jitter_seconds,
        )
        if result.created:
            self.db.log_event("INFO", "JOB_QUEUE", f"{description} 已排队，延迟 {jitter_seconds} 秒启动")

    def notify_new_job(self) -> None:
        self.wakeup.set()

    async def _dispatcher_loop(self) -> None:
        while not self.stop_event.is_set():
            if len(self.running_tasks) >= self.config.max_parallel_admin_jobs:
                await self._wait_for_wakeup(self.config.scheduler_poll_seconds)
                continue
            job = self.db.claim_next_job()
            if job is None:
                await self._wait_for_wakeup(self.config.scheduler_poll_seconds)
                continue
            task = asyncio.create_task(self._run_job(job))
            self.running_tasks.add(task)
            task.add_done_callback(self.running_tasks.discard)

    async def _run_job(self, job: AdminJob) -> None:
        self.db.log_event("INFO", "JOB_START", f"{job.kind}#{job.id}")
        try:
            result = await self.executors.execute(job)
        except Exception as exc:
            self.db.fail_job(job.id, str(exc), retry=False)
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

    async def _wait_for_wakeup(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self.wakeup.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        self.wakeup.clear()

    async def _housekeeping_loop(self) -> None:
        while not self.stop_event.is_set():
            self.db.cleanup_finished_jobs()
            await asyncio.sleep(120)
