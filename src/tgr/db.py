from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS folder_rules (
    folder_name TEXT PRIMARY KEY,
    folder_id INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    notify_channel_id INTEGER,
    alert_channel_id INTEGER,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_name TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    pattern TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(folder_name, rule_name)
);
CREATE TABLE IF NOT EXISTS routes (
    folder_name TEXT PRIMARY KEY,
    pattern TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS folder_cache (
    folder_name TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    chat_title TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(folder_name, chat_id)
);
CREATE TABLE IF NOT EXISTS route_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_name TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    chat_title TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(folder_name, chat_id)
);
CREATE TABLE IF NOT EXISTS admin_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    run_after TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    result_summary TEXT
);
CREATE TABLE IF NOT EXISTS runtime_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ops_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@dataclass(slots=True)
class AdminJob:
    id: int
    job_type: str
    payload: dict[str, Any]
    status: str
    run_after: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    error: str | None
    result_summary: str | None


@dataclass(slots=True)
class RouteTask:
    id: int
    folder_name: str
    chat_id: int
    chat_title: str
    status: str
    detail: str
    created_at: str
    updated_at: str


class RadarDB:
    def __init__(self, path: Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            now = self._now()
            for key, value in {
                'revision': '1',
                'last_core_reload': '',
                'last_sync': '',
                'last_route_scan': '',
            }.items():
                conn.execute(
                    "INSERT OR IGNORE INTO runtime_state(key, value, updated_at) VALUES (?, ?, ?)",
                    (key, value, now),
                )

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def try_log_event(self, level: str, action: str, detail: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO ops_log(level, action, detail, created_at) VALUES (?, ?, ?, ?)",
                    (level, action, detail[:1000], self._now()),
                )
        except Exception:
            pass

    def log_event(self, level: str, action: str, detail: str) -> None:
        self.try_log_event(level, action, detail)

    def list_logs(self, limit: int = 30) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM ops_log ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()

    def is_empty(self) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM folder_rules").fetchone()
            return int(row['c']) == 0

    def bump_revision(self, conn: sqlite3.Connection | None = None) -> int:
        now = self._now()
        owns = conn is None
        if owns:
            ctx = self.tx()
            conn = ctx.__enter__()
        assert conn is not None
        row = conn.execute("SELECT value FROM runtime_state WHERE key='revision'").fetchone()
        current = int(row['value']) if row else 0
        current += 1
        conn.execute(
            "INSERT INTO runtime_state(key, value, updated_at) VALUES ('revision', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (str(current), now),
        )
        if owns:
            ctx.__exit__(None, None, None)
        return current

    def get_revision(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM runtime_state WHERE key='revision'").fetchone()
            return int(row['value']) if row and row['value'] else 0

    def set_runtime_value(self, key: str, value: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO runtime_state(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, self._now()),
            )

    def get_runtime_value(self, key: str, default: str = '') -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM runtime_state WHERE key=?", (key,)).fetchone()
            return str(row['value']) if row else default

    def upsert_folder(
        self,
        folder_name: str,
        *,
        folder_id: int | None,
        enabled: bool = True,
        notify_channel_id: int | None = None,
        alert_channel_id: int | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        now = self._now()
        owns = conn is None
        if owns:
            ctx = self.tx()
            conn = ctx.__enter__()
        assert conn is not None
        conn.execute(
            "INSERT INTO folder_rules(folder_name, folder_id, enabled, notify_channel_id, alert_channel_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(folder_name) DO UPDATE SET "
            "folder_id=excluded.folder_id, enabled=excluded.enabled, "
            "notify_channel_id=COALESCE(excluded.notify_channel_id, folder_rules.notify_channel_id), "
            "alert_channel_id=COALESCE(excluded.alert_channel_id, folder_rules.alert_channel_id), "
            "updated_at=excluded.updated_at",
            (folder_name, folder_id, 1 if enabled else 0, notify_channel_id, alert_channel_id, now),
        )
        self.bump_revision(conn)
        if owns:
            ctx.__exit__(None, None, None)

    def set_folder_enabled(self, folder_name: str, enabled: bool) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE folder_rules SET enabled=?, updated_at=? WHERE folder_name=?",
                (1 if enabled else 0, self._now(), folder_name),
            )
            self.bump_revision(conn)

    def set_folder_notify(self, folder_name: str, notify_channel_id: int | None) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE folder_rules SET notify_channel_id=?, updated_at=? WHERE folder_name=?",
                (notify_channel_id, self._now(), folder_name),
            )
            self.bump_revision(conn)

    def set_folder_alert(self, folder_name: str, alert_channel_id: int | None) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE folder_rules SET alert_channel_id=?, updated_at=? WHERE folder_name=?",
                (alert_channel_id, self._now(), folder_name),
            )
            self.bump_revision(conn)

    def list_folders(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM folder_rules ORDER BY folder_name COLLATE NOCASE").fetchall()

    def get_folder(self, folder_name: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM folder_rules WHERE folder_name=?", (folder_name,)).fetchone()

    def delete_folder(self, folder_name: str) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM rules WHERE folder_name=?", (folder_name,))
            conn.execute("DELETE FROM folder_cache WHERE folder_name=?", (folder_name,))
            conn.execute("DELETE FROM routes WHERE folder_name=?", (folder_name,))
            conn.execute("DELETE FROM folder_rules WHERE folder_name=?", (folder_name,))
            self.bump_revision(conn)

    def replace_folder_cache(self, folder_name: str, items: list[tuple[int, str]], *, conn: sqlite3.Connection | None = None) -> None:
        now = self._now()
        owns = conn is None
        if owns:
            ctx = self.tx()
            conn = ctx.__enter__()
        assert conn is not None
        conn.execute("DELETE FROM folder_cache WHERE folder_name=?", (folder_name,))
        conn.executemany(
            "INSERT INTO folder_cache(folder_name, chat_id, chat_title, updated_at) VALUES (?, ?, ?, ?)",
            [(folder_name, int(chat_id), title, now) for chat_id, title in items],
        )
        self.bump_revision(conn)
        if owns:
            ctx.__exit__(None, None, None)

    def upsert_folder_cache(self, folder_name: str, chat_id: int, chat_title: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO folder_cache(folder_name, chat_id, chat_title, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(folder_name, chat_id) DO UPDATE SET chat_title=excluded.chat_title, updated_at=excluded.updated_at",
                (folder_name, int(chat_id), chat_title, self._now()),
            )
            self.bump_revision(conn)

    def list_folder_cache(self, folder_name: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM folder_cache WHERE folder_name=? ORDER BY LOWER(chat_title), chat_id",
                (folder_name,),
            ).fetchall()

    def list_all_cache_chat_ids(self) -> set[int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT chat_id FROM folder_cache").fetchall()
            return {int(row['chat_id']) for row in rows}

    def count_cache_for_folder(self, folder_name: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM folder_cache WHERE folder_name=?", (folder_name,)).fetchone()
            return int(row['c']) if row else 0

    def upsert_rule(self, folder_name: str, rule_name: str, pattern: str, enabled: bool = True) -> None:
        now = self._now()
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO rules(folder_name, rule_name, pattern, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(folder_name, rule_name) DO UPDATE SET pattern=excluded.pattern, enabled=excluded.enabled, updated_at=excluded.updated_at",
                (folder_name, rule_name, pattern, 1 if enabled else 0, now, now),
            )
            self.bump_revision(conn)

    def list_rules(self, folder_name: str | None = None, enabled_only: bool = False) -> list[sqlite3.Row]:
        query = "SELECT * FROM rules"
        params: list[Any] = []
        clauses = []
        if folder_name is not None:
            clauses.append("folder_name=?")
            params.append(folder_name)
        if enabled_only:
            clauses.append("enabled=1")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY folder_name COLLATE NOCASE, rule_name COLLATE NOCASE"
        with self._connect() as conn:
            return conn.execute(query, params).fetchall()

    def get_rule(self, folder_name: str, rule_name: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM rules WHERE folder_name=? AND rule_name=?",
                (folder_name, rule_name),
            ).fetchone()

    def count_rules_for_folder(self, folder_name: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM rules WHERE folder_name=?", (folder_name,)).fetchone()
            return int(row['c']) if row else 0

    def delete_rule(self, folder_name: str, rule_name: str) -> bool:
        with self.tx() as conn:
            cur = conn.execute("DELETE FROM rules WHERE folder_name=? AND rule_name=?", (folder_name, rule_name))
            changed = cur.rowcount > 0
            if changed:
                self.bump_revision(conn)
            return changed

    def upsert_route(self, folder_name: str, pattern: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO routes(folder_name, pattern, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(folder_name) DO UPDATE SET pattern=excluded.pattern, updated_at=excluded.updated_at",
                (folder_name, pattern, self._now()),
            )
            self.bump_revision(conn)

    def list_routes(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM routes ORDER BY folder_name COLLATE NOCASE").fetchall()

    def delete_route(self, folder_name: str) -> bool:
        with self.tx() as conn:
            cur = conn.execute("DELETE FROM routes WHERE folder_name=?", (folder_name,))
            changed = cur.rowcount > 0
            if changed:
                self.bump_revision(conn)
            return changed

    def add_route_task(self, folder_name: str, chat_id: int, chat_title: str, detail: str, status: str = 'queued') -> None:
        with self.tx() as conn:
            now = self._now()
            conn.execute(
                "INSERT INTO route_tasks(folder_name, chat_id, chat_title, status, detail, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(folder_name, chat_id) DO UPDATE SET status=excluded.status, detail=excluded.detail, updated_at=excluded.updated_at",
                (folder_name, int(chat_id), chat_title, status, detail, now, now),
            )

    def list_route_tasks(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM route_tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def create_job(self, job_type: str, payload: dict[str, Any], run_after: str | None = None) -> int:
        with self.tx() as conn:
            cur = conn.execute(
                "INSERT INTO admin_jobs(job_type, payload, status, run_after, created_at, updated_at) VALUES (?, ?, 'queued', ?, ?, ?)",
                (job_type, json.dumps(payload, ensure_ascii=False), run_after, self._now(), self._now()),
            )
            return int(cur.lastrowid)

    def list_jobs(self, limit: int = 30) -> list[AdminJob]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM admin_jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [self._row_to_job(r) for r in rows]

    def get_due_jobs(self, limit: int = 10) -> list[AdminJob]:
        now = self._now()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM admin_jobs WHERE status='queued' AND (run_after IS NULL OR run_after<=?) ORDER BY id ASC LIMIT ?",
                (now, limit),
            ).fetchall()
            return [self._row_to_job(r) for r in rows]

    def claim_job(self, job_id: int) -> bool:
        with self.tx() as conn:
            cur = conn.execute(
                "UPDATE admin_jobs SET status='running', started_at=?, updated_at=? WHERE id=? AND status='queued'",
                (self._now(), self._now(), job_id),
            )
            return cur.rowcount > 0

    def finish_job(self, job_id: int, *, success: bool, summary: str, error: str | None = None) -> None:
        status = 'done' if success else 'failed'
        with self.tx() as conn:
            conn.execute(
                "UPDATE admin_jobs SET status=?, finished_at=?, updated_at=?, error=?, result_summary=? WHERE id=?",
                (status, self._now(), self._now(), error, summary, job_id),
            )

    def mark_core_reloaded(self) -> None:
        self.set_runtime_value('last_core_reload', self._now())

    def build_monitor_snapshot(self, global_alert_channel_id: int | None) -> dict[int, list[dict[str, Any]]]:
        folders = self.list_folders()
        rules_by_folder: dict[str, list[sqlite3.Row]] = {}
        for row in self.list_rules(enabled_only=True):
            rules_by_folder.setdefault(str(row['folder_name']), []).append(row)
        result: dict[int, list[dict[str, Any]]] = {}
        for folder in folders:
            if int(folder['enabled']) != 1:
                continue
            folder_name = str(folder['folder_name'])
            target = folder['notify_channel_id'] or folder['alert_channel_id'] or global_alert_channel_id
            if target is None:
                continue
            rules = rules_by_folder.get(folder_name, [])
            if not rules:
                continue
            for chat in self.list_folder_cache(folder_name):
                bucket = result.setdefault(int(chat['chat_id']), [])
                for rule in rules:
                    bucket.append({
                        'folder_name': folder_name,
                        'rule_name': str(rule['rule_name']),
                        'pattern': str(rule['pattern']),
                        'target_id': int(target),
                    })
        return result

    def export_legacy_snapshot(self) -> dict[str, Any]:
        folder_rules: dict[str, Any] = {}
        for folder in self.list_folders():
            folder_name = str(folder['folder_name'])
            rule_map = {
                str(rule['rule_name']): str(rule['pattern'])
                for rule in self.list_rules(folder_name)
            }
            folder_rules[folder_name] = {
                'folder_id': folder['folder_id'],
                'enabled': bool(folder['enabled']),
                'notify_channel_id': folder['notify_channel_id'],
                'alert_channel_id': folder['alert_channel_id'],
                'rules': rule_map,
            }
        auto_route_rules = {str(row['folder_name']): str(row['pattern']) for row in self.list_routes()}
        system_cache: dict[str, dict[str, str]] = {}
        for folder in self.list_folders():
            folder_name = str(folder['folder_name'])
            system_cache[folder_name] = {
                str(row['chat_id']): str(row['chat_title'])
                for row in self.list_folder_cache(folder_name)
            }
        return {
            'folder_rules': folder_rules,
            'auto_route_rules': auto_route_rules,
            '_system_cache': system_cache,
        }

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> AdminJob:
        payload = json.loads(row['payload']) if row['payload'] else {}
        return AdminJob(
            id=int(row['id']),
            job_type=str(row['job_type']),
            payload=payload,
            status=str(row['status']),
            run_after=row['run_after'],
            created_at=str(row['created_at']),
            updated_at=str(row['updated_at']),
            started_at=row['started_at'],
            finished_at=row['finished_at'],
            error=row['error'],
            result_summary=row['result_summary'],
        )
