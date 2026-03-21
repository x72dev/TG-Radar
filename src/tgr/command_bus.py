from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from .db import RadarDB


class CommandBus:
    def __init__(self, db: RadarDB, notifier: Callable[[], None] | None = None) -> None:
        self.db = db
        self.notifier = notifier

    def submit(self, job_type: str, payload: dict[str, Any], *, delay_seconds: int = 0) -> int:
        run_after = None
        if delay_seconds > 0:
            run_after = (datetime.now() + timedelta(seconds=delay_seconds)).strftime('%Y-%m-%d %H:%M:%S')
        job_id = self.db.create_job(job_type, payload, run_after=run_after)
        if self.notifier is not None:
            self.notifier()
        return job_id
