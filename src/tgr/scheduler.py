from __future__ import annotations

import asyncio
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .executors import execute_job
from .telegram_utils import panel, section


class AdminScheduler:
    def __init__(self, app) -> None:
        self.app = app
        self._wake = asyncio.Event()
        self._scheduler = AsyncIOScheduler(timezone='UTC')
        self._running_tasks: set[asyncio.Task] = set()

    def notify_new_job(self) -> None:
        self._wake.set()

    def _schedule_recurring_jobs(self) -> None:
        def _hhmm(value: str) -> tuple[int, int]:
            hour, minute = value.strip().split(':', 1)
            return int(hour), int(minute)

        if self.app.config.auto_sync_enabled:
            h, m = _hhmm(self.app.config.auto_sync_time)
            self._scheduler.add_job(lambda: self.app.command_bus.submit('sync', {'source': 'cron'}), CronTrigger(hour=h, minute=m), id='auto-sync', replace_existing=True)
        if self.app.config.auto_route_enabled:
            h, m = _hhmm(self.app.config.auto_route_time)
            self._scheduler.add_job(lambda: self.app.command_bus.submit('routescan', {'source': 'cron'}), CronTrigger(hour=h, minute=m), id='auto-routescan', replace_existing=True)

    async def run(self) -> None:
        self._schedule_recurring_jobs()
        self._scheduler.start()
        try:
            while not self.app.stop_event.is_set():
                await self._drain_jobs_once()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
        finally:
            self._scheduler.shutdown(wait=False)
            for task in list(self._running_tasks):
                task.cancel()
            await asyncio.gather(*self._running_tasks, return_exceptions=True)

    async def _drain_jobs_once(self) -> None:
        max_jobs = max(1, self.app.config.max_parallel_admin_jobs - len(self._running_tasks))
        if max_jobs <= 0:
            return
        for job in self.app.db.get_due_jobs(limit=max_jobs):
            if not self.app.db.claim_job(job.id):
                continue
            task = asyncio.create_task(self._run_job(job))
            self._running_tasks.add(task)
            task.add_done_callback(self._running_tasks.discard)

    async def _run_job(self, job) -> None:
        try:
            result = await execute_job(self.app, job)
            self.app.db.finish_job(job.id, success=result.success, summary=result.summary(), error=None if result.success else result.summary())
            await self.app.notify_job_result(job, result)
        except Exception as exc:
            self.app.logger.exception('job %s failed: %s', job.id, exc)
            self.app.db.finish_job(job.id, success=False, summary=str(exc), error=str(exc))
            await self.app.notify_job_failure(job, exc)
