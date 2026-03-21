from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from .db import RadarDB


@dataclass(frozen=True)
class JobSubmitResult:
    job_id: int | None
    created: bool
    kind: str
    dedupe_key: str | None


class CommandBus:
    def __init__(self, db: RadarDB, notifier: Callable[[], None] | None = None) -> None:
        self.db = db
        self.notifier = notifier

    @staticmethod
    def _to_run_after(delay_seconds: float | int | None) -> str | None:
        if not delay_seconds or float(delay_seconds) <= 0:
            return None
        return (datetime.now() + timedelta(seconds=float(delay_seconds))).strftime("%Y-%m-%d %H:%M:%S")

    def submit(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        priority: int = 100,
        dedupe_key: str | None = None,
        origin: str = "system",
        visible: bool = True,
        delay_seconds: float | int | None = None,
    ) -> JobSubmitResult:
        job_id, created = self.db.enqueue_job(
            kind,
            payload or {},
            priority=priority,
            dedupe_key=dedupe_key,
            origin=origin,
            visible=visible,
            run_after=self._to_run_after(delay_seconds),
        )
        if created and self.notifier:
            try:
                self.notifier()
            except Exception:
                pass
        return JobSubmitResult(job_id=job_id, created=created, kind=kind, dedupe_key=dedupe_key)
