from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timedelta
from typing import Any

from .db import AdminJob
from .executors import AdminExecutors


class AdminScheduler:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.db = app.db
        self.config = app.config
        self.executors = AdminExecutors(app)
        self.stop_event = app.stop_event
        self.running_tasks: set[asyncio.Task] = set()
        self.wakeup = asyncio.Event()

    async def run(self) -> None:
        tasks = [
            asyncio.create_task(self._dispatcher_loop()),
            asyncio.create_task(self._daily_sync_loop()),
            asyncio.create_task(self._daily_route_loop()),
            asyncio.create_task(self._housekeeping_loop()),
        ]
        await self.stop_event.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.running_tasks:
            await asyncio.gather(*list(self.running_tasks), return_exceptions=True)

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
        except Exception as exc:  # pragma: no cover
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

    async def _daily_sync_loop(self) -> None:
        if not self.config.auto_sync_enabled:
            return
        await self._daily_loop(
            kind="sync_auto",
            at_time=self.config.auto_sync_time,
            dedupe_key="sync_auto",
            priority=90,
            description="自动同步",
        )

    async def _daily_route_loop(self) -> None:
        if not self.config.auto_route_enabled:
            return
        await self._daily_loop(
            kind="route_scan",
            at_time=self.config.auto_route_time,
            dedupe_key="route_scan",
            priority=95,
            description="自动收纳扫描",
        )

    async def _daily_loop(self, *, kind: str, at_time: str, dedupe_key: str, priority: int, description: str) -> None:
        while not self.stop_event.is_set():
            delay = self._seconds_until_next_window(at_time, self.config.daily_jitter_minutes)
            await self._sleep_interruptible(delay)
            await self._wait_until_idle()
            result = self.app.command_bus.submit(
                kind,
                priority=priority,
                dedupe_key=dedupe_key,
                origin="scheduler",
                visible=False,
            )
            if result.created:
                self.db.log_event("INFO", "JOB_QUEUE", f"{description} 已进入后台队列")
            await self._sleep_interruptible(60)

    def _seconds_until_next_window(self, base_hhmm: str, jitter_minutes: int) -> float:
        now = datetime.now()
        hour, minute = [int(x) for x in base_hhmm.split(":", 1)]
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        if jitter_minutes > 0:
            target += timedelta(minutes=random.randint(0, jitter_minutes))
        return max(1.0, (target - now).total_seconds())

    async def _wait_until_idle(self) -> None:
        while not self.stop_event.is_set():
            recent_cmd = time.monotonic() - self.app.last_command_ts
            heavy_busy = self.db.count_open_jobs("sync_manual") + self.db.count_open_jobs("sync_auto") + self.db.count_open_jobs("route_scan") + self.db.count_open_jobs("update_repo") + self.db.count_open_jobs("restart_services")
            if recent_cmd >= self.config.idle_grace_seconds and heavy_busy == 0:
                return
            await asyncio.sleep(5)

    async def _wait_for_wakeup(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self.wakeup.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        self.wakeup.clear()

    async def _sleep_interruptible(self, seconds: float) -> None:
        end = time.monotonic() + max(0.0, seconds)
        while not self.stop_event.is_set():
            remain = end - time.monotonic()
            if remain <= 0:
                return
            await asyncio.sleep(min(5.0, remain))

    async def _housekeeping_loop(self) -> None:
        while not self.stop_event.is_set():
            self.db.cleanup_finished_jobs()
            await asyncio.sleep(120)
