"""Controlled sub-interfaces exposed to plugins through PluginContext.

These classes whitelist specific methods from core, so plugins cannot
accidentally call internal methods or break the system.
"""
from __future__ import annotations

from typing import Any


class PluginDB:
    """Whitelisted database access for plugins."""

    def __init__(self, app: Any) -> None:
        self._app = app

    @property
    def _db(self):
        return self._app.db

    # ── Folder read ──
    def list_folders(self):
        return self._db.list_folders()

    def get_folder(self, name: str):
        return self._db.get_folder(name)

    def count_cache_for_folder(self, name: str) -> int:
        return self._db.count_cache_for_folder(name)

    def count_rules_for_folder(self, name: str) -> int:
        return self._db.count_rules_for_folder(name)

    def count_cache_all_folders(self) -> dict[str, int]:
        return self._db.count_cache_all_folders()

    def count_rules_all_folders(self) -> dict[str, int]:
        return self._db.count_rules_all_folders()

    # ── Rule read ──
    def get_rules_for_folder(self, folder_name: str):
        return self._db.get_rules_for_folder(folder_name)

    # ── Rule write ──
    def upsert_folder(self, name, folder_id, **kwargs):
        return self._db.upsert_folder(name, folder_id, **kwargs)

    def set_folder_enabled(self, name: str, enabled: bool):
        return self._db.set_folder_enabled(name, enabled)

    def set_folder_alert_channel(self, name: str, channel_id: int | None):
        return self._db.set_folder_alert_channel(name, channel_id)

    def upsert_rule(self, folder, rule, pattern, **kwargs):
        return self._db.upsert_rule(folder, rule, pattern, **kwargs)

    def delete_rule(self, folder, rule):
        return self._db.delete_rule(folder, rule)

    def update_rule_pattern(self, folder, rule, pattern):
        return self._db.update_rule_pattern(folder, rule, pattern)

    # ── Route ──
    def list_routes(self):
        return self._db.list_routes()

    def set_route(self, folder: str, pattern: str):
        return self._db.set_route(folder, pattern)

    def delete_route(self, folder: str) -> bool:
        return self._db.delete_route(folder)

    # ── Stats ──
    def get_runtime_stats(self) -> dict[str, str]:
        return self._db.get_runtime_stats()

    def build_target_map(self, global_alert_channel_id):
        return self._db.build_target_map(global_alert_channel_id)

    def increment_hit(self, folder_name: str):
        return self._db.increment_hit(folder_name)

    def pending_route_count(self) -> int:
        return self._db.pending_route_count()

    # ── Logging ──
    def log_event(self, level: str, action: str, detail: str):
        return self._db.log_event(level, action, detail)

    def recent_logs_for_panel(self, **kwargs):
        return self._db.recent_logs_for_panel(**kwargs)

    # ── Jobs ──
    def list_open_jobs(self, limit: int = 20):
        return self._db.list_open_jobs(limit)


class PluginUI:
    """HTML rendering toolkit for plugins."""

    def __init__(self) -> None:
        from . import telegram_utils as _tu
        self._tu = _tu

    def escape(self, value) -> str:
        return self._tu.escape(value)

    def html_code(self, text) -> str:
        return self._tu.html_code(text)

    def bullet(self, label, value=None, **kwargs) -> str:
        return self._tu.bullet(label, value, **kwargs)

    def soft_kv(self, label, value=None) -> str:
        return self._tu.soft_kv(label, value)

    def section(self, title, rows) -> str:
        return self._tu.section(title, rows)

    def panel(self, title, sections, footer=None) -> str:
        return self._tu.panel(title, sections, footer)

    def blockquote_preview(self, text, limit=900) -> str:
        return self._tu.blockquote_preview(text, limit)

    def format_duration(self, seconds) -> str:
        return self._tu.format_duration(seconds)

    def shorten_path(self, path, keep=2) -> str:
        return self._tu.shorten_path(path, keep)


class PluginBus:
    """Command bus access for plugins."""

    def __init__(self, app: Any) -> None:
        self._app = app

    def submit_job(self, kind: str, payload: dict | None = None, **kwargs):
        if hasattr(self._app, "command_bus"):
            return self._app.command_bus.submit(kind, payload, **kwargs)
        return None
