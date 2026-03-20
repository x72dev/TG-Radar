from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from .db import AdminJob
from .executors import AdminExecutors, JobResult


class AdminScheduler:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.db = app.db
        self.config = app.config
        self.executors = AdminExecutors(app)
        self.stop_event = app.stop_event
        self.worker_semaphore = asyncio.Semaphore(self.config.max_parallel_admin_jobs)
        self.running_tasks: set[asyncio.Task] = set()
        self.last_snapshot_request = 0.0

    async def run(self) -> None:
        tasks = [
            asyncio.create_task(self._dispatcher_loop()),
            asyncio.create_task(self._auto_sync_loop()),
            asyncio.create_task(self._route_scan_loop()),
            asyncio.create_task(self._housekeeping_loop()),
        ]
        await self.stop_event.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.running_tasks:
            await asyncio.gather(*list(self.running_tasks), return_exceptions=True)

    async def _dispatcher_loop(self) -> None:
        while not self.stop_event.is_set():
            if len(self.running_tasks) >= self.config.max_parallel_admin_jobs:
                await asyncio.sleep(self.config.scheduler_poll_seconds)
                continue
            job = self.db.claim_next_job()
            if job is None:
                await asyncio.sleep(self.config.scheduler_poll_seconds)
                continue
            task = asyncio.create_task(self._run_job(job))
            self.running_tasks.add(task)
            task.add_done_callback(self.running_tasks.discard)

    async def _run_job(self, job: AdminJob) -> None:
        async with self.worker_semaphore:
            try:
                result = await self.executors.execute(job)
            except Exception as exc:  # pragma: no cover - runtime behavior
                self.db.fail_job(job.id, str(exc), retry=False)
                self.db.log_event("ERROR", "COMMAND", f"{job.kind}: {exc}")
                await self.app.notify_job_failure(job, exc)
                return

            if result.log_action:
                self.db.log_event(result.log_level, result.log_action, result.detail or result.summary)
            self.db.finish_job(job.id)
            await self.app.after_job(job, result)

    async def _auto_sync_loop(self) -> None:
        jitter = self.config.sync_auto_jitter_seconds
        if jitter:
            await asyncio.sleep(random.randint(0, jitter))
        while not self.stop_event.is_set():
            if time.monotonic() - self.app.last_command_ts > 2:
                self.app.command_bus.submit(
                    "sync_auto",
                    priority=90,
                    dedupe_key="sync_auto",
                    origin="scheduler",
                    visible=False,
                )
            await asyncio.sleep(self.config.sync_interval_seconds)

    async def _route_scan_loop(self) -> None:
        while not self.stop_event.is_set():
            if time.monotonic() - self.app.last_command_ts > 2:
                self.app.command_bus.submit(
                    "route_scan",
                    priority=95,
                    dedupe_key="route_scan",
                    origin="scheduler",
                    visible=False,
                )
            await asyncio.sleep(self.config.route_scan_interval_seconds)

    async def _housekeeping_loop(self) -> None:
        while not self.stop_event.is_set():
            self.db.cleanup_finished_jobs()
            await asyncio.sleep(120)
