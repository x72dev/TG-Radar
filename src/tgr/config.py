from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PUBLIC_DEFAULT_CONFIG: dict[str, Any] = {
    "api_id": 1234567,
    "api_hash": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "global_alert_channel_id": None,
    "notify_channel_id": None,
    "cmd_prefix": "-",
    "service_name_prefix": "tg-radar",
    "operation_mode": "stable",
    "auto_sync_enabled": True,
    "auto_sync_time": "03:40",
    "auto_route_enabled": True,
    "auto_route_time": "04:20",
    "panel_auto_delete_seconds": 45,
    "notify_auto_delete_seconds": 0,
    "recycle_fallback_command_seconds": 8,
    "repo_url": "https://github.com/chenmo8848/TG-Radar.git",
    "plugins_repo_url": "https://github.com/chenmo8848/TG-Radar-Plugins.git",
    "plugins_dir": "./plugins-external/TG-Radar-Plugins/plugins",
    "auto_route_rules": {},
    "folder_rules": {},
    "_system_cache": {},
}

MODE_INTERNALS: dict[str, dict[str, Any]] = {
    "stable": {
        "scheduler_poll_seconds": 1,
        "snapshot_flush_debounce_seconds": 10,
        "reload_debounce_seconds": 1.2,
        "manual_heavy_delay_seconds": 4,
        "restart_delay_seconds": 2,
        "update_delay_seconds": 3,
        "route_apply_delay_seconds": 2,
        "max_parallel_admin_jobs": 1,
        "idle_grace_seconds": 45,
        "daily_jitter_minutes": 15,
        "route_batch_size": 30,
        "sync_batch_size": 80,
        "batch_sleep_min_seconds": 0.5,
        "batch_sleep_max_seconds": 1.5,
        # FIX BUG-05: use -1 to disable, positive to enable
        "revision_poll_seconds": -1,
    },
    "balanced": {
        "scheduler_poll_seconds": 1,
        "snapshot_flush_debounce_seconds": 6,
        "reload_debounce_seconds": 0.8,
        "manual_heavy_delay_seconds": 2,
        "restart_delay_seconds": 1.5,
        "update_delay_seconds": 2,
        "route_apply_delay_seconds": 1,
        "max_parallel_admin_jobs": 1,
        "idle_grace_seconds": 20,
        "daily_jitter_minutes": 8,
        "route_batch_size": 45,
        "sync_batch_size": 120,
        "batch_sleep_min_seconds": 0.25,
        "batch_sleep_max_seconds": 0.9,
        "revision_poll_seconds": -1,
    },
    "aggressive": {
        "scheduler_poll_seconds": 1,
        "snapshot_flush_debounce_seconds": 3,
        "reload_debounce_seconds": 0.4,
        "manual_heavy_delay_seconds": 1,
        "restart_delay_seconds": 1,
        "update_delay_seconds": 1,
        "route_apply_delay_seconds": 0.5,
        "max_parallel_admin_jobs": 1,
        "idle_grace_seconds": 10,
        "daily_jitter_minutes": 3,
        "route_batch_size": 60,
        "sync_batch_size": 180,
        "batch_sleep_min_seconds": 0.1,
        "batch_sleep_max_seconds": 0.5,
        "revision_poll_seconds": 3,
    },
}

LEGACY_KEYS_TO_DROP = {
    "scheduler_poll_seconds",
    "snapshot_flush_debounce_seconds",
    "reload_debounce_seconds",
    "manual_heavy_delay_seconds",
    "restart_delay_seconds",
    "update_delay_seconds",
    "route_apply_delay_seconds",
    "max_parallel_admin_jobs",
    "idle_grace_seconds",
    "daily_jitter_minutes",
    "route_batch_size",
    "sync_batch_size",
    "batch_sleep_min_seconds",
    "batch_sleep_max_seconds",
    "route_worker_interval_seconds",
    "route_scan_interval_seconds",
    "sync_interval_seconds",
    "scheduler_mode",
    "revision_poll_seconds",
    "sync_auto_jitter_seconds",
}

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass(frozen=True)
class AppConfig:
    work_dir: Path
    api_id: int
    api_hash: str
    global_alert_channel_id: int | None
    notify_channel_id: int | None
    cmd_prefix: str
    service_name_prefix: str
    operation_mode: str
    auto_sync_enabled: bool
    auto_sync_time: str
    auto_route_enabled: bool
    auto_route_time: str
    panel_auto_delete_seconds: int
    notify_auto_delete_seconds: int
    recycle_fallback_command_seconds: int
    repo_url: str | None
    plugins_repo_url: str | None
    plugins_dir: str

    scheduler_poll_seconds: int
    snapshot_flush_debounce_seconds: int
    reload_debounce_seconds: float
    manual_heavy_delay_seconds: float
    restart_delay_seconds: float
    update_delay_seconds: float
    route_apply_delay_seconds: float
    max_parallel_admin_jobs: int
    idle_grace_seconds: int
    daily_jitter_minutes: int
    route_batch_size: int
    sync_batch_size: int
    batch_sleep_min_seconds: float
    batch_sleep_max_seconds: float
    revision_poll_seconds: int

    @property
    def runtime_dir(self) -> Path:
        return self.work_dir / "runtime"

    @property
    def db_path(self) -> Path:
        return self.runtime_dir / "radar.db"

    @property
    def logs_dir(self) -> Path:
        return self.runtime_dir / "logs"

    @property
    def sessions_dir(self) -> Path:
        return self.runtime_dir / "sessions"

    @property
    def backups_dir(self) -> Path:
        return self.runtime_dir / "backups"

    @property
    def plugins_root(self) -> Path:
        value = self.plugins_dir or "./plugins-external/TG-Radar-Plugins/plugins"
        path = Path(value)
        return (self.work_dir / path).resolve() if not path.is_absolute() else path

    @property
    def admin_session(self) -> Path:
        return self.sessions_dir / "tg_radar_admin"

    @property
    def admin_worker_session(self) -> Path:
        return self.sessions_dir / "tg_radar_admin_worker"

    @property
    def core_session(self) -> Path:
        return self.sessions_dir / "tg_radar_core"


def _normalize_int(value: Any) -> int | None:
    if value in (None, "", "null", "None", "off", "OFF"):
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _normalize_non_negative_int(value: Any, default: int) -> int:
    try:
        normalized = int(str(value).strip())
    except Exception:
        normalized = default
    return max(0, normalized)


def _normalize_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _normalize_mode(value: Any) -> str:
    mode = str(value or "stable").strip().lower()
    return mode if mode in MODE_INTERNALS else "stable"


def _normalize_time_hhmm(value: Any, default: str) -> str:
    raw = str(value or default).strip()
    parts = raw.split(":", 1)
    if len(parts) != 2:
        return default
    try:
        hour = max(0, min(23, int(parts[0])))
        minute = max(0, min(59, int(parts[1])))
    except Exception:
        return default
    return f"{hour:02d}:{minute:02d}"


def _normalize_service_name(value: Any) -> str:
    """SEC-01: validate service_name_prefix to prevent shell injection."""
    name = str(value or "tg-radar").strip()
    if not _SAFE_NAME_RE.match(name):
        return "tg-radar"
    return name


def read_config_data(work_dir: Path) -> dict[str, Any]:
    path = work_dir / "config.json"
    raw: dict[str, Any]
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = {}
    raw = {k: v for k, v in raw.items() if not (k.startswith("_说明_") or k.startswith("_comment_") or k in LEGACY_KEYS_TO_DROP)}
    data = dict(PUBLIC_DEFAULT_CONFIG)
    data.update(raw)
    data["api_id"] = int(data.get("api_id") or 0)
    data["api_hash"] = str(data.get("api_hash") or "")
    data["global_alert_channel_id"] = _normalize_int(data.get("global_alert_channel_id"))
    data["notify_channel_id"] = _normalize_int(data.get("notify_channel_id"))
    data["cmd_prefix"] = str(data.get("cmd_prefix") or "-")
    data["service_name_prefix"] = _normalize_service_name(data.get("service_name_prefix"))
    data["operation_mode"] = _normalize_mode(data.get("operation_mode"))
    data["auto_sync_enabled"] = _normalize_bool(data.get("auto_sync_enabled"), True)
    data["auto_route_enabled"] = _normalize_bool(data.get("auto_route_enabled"), True)
    data["auto_sync_time"] = _normalize_time_hhmm(data.get("auto_sync_time"), PUBLIC_DEFAULT_CONFIG["auto_sync_time"])
    data["auto_route_time"] = _normalize_time_hhmm(data.get("auto_route_time"), PUBLIC_DEFAULT_CONFIG["auto_route_time"])
    data["panel_auto_delete_seconds"] = _normalize_non_negative_int(data.get("panel_auto_delete_seconds"), PUBLIC_DEFAULT_CONFIG["panel_auto_delete_seconds"])
    data["notify_auto_delete_seconds"] = _normalize_non_negative_int(data.get("notify_auto_delete_seconds"), PUBLIC_DEFAULT_CONFIG["notify_auto_delete_seconds"])
    data["recycle_fallback_command_seconds"] = _normalize_non_negative_int(data.get("recycle_fallback_command_seconds"), PUBLIC_DEFAULT_CONFIG["recycle_fallback_command_seconds"])
    data["repo_url"] = str(data.get("repo_url") or PUBLIC_DEFAULT_CONFIG["repo_url"])
    data["plugins_repo_url"] = str(data.get("plugins_repo_url") or PUBLIC_DEFAULT_CONFIG["plugins_repo_url"])
    data["plugins_dir"] = str(data.get("plugins_dir") or PUBLIC_DEFAULT_CONFIG["plugins_dir"])
    data["auto_route_rules"] = data.get("auto_route_rules") or {}
    data["folder_rules"] = data.get("folder_rules") or {}
    data["_system_cache"] = data.get("_system_cache") or {}
    return data


def save_config_data(work_dir: Path, data: dict[str, Any]) -> Path:
    config_path = work_dir / "config.json"
    normalized = dict(PUBLIC_DEFAULT_CONFIG)
    normalized.update(data)
    payload = {k: normalized[k] for k in PUBLIC_DEFAULT_CONFIG.keys()}
    tmp = config_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    tmp.replace(config_path)
    return config_path


def update_config_data(work_dir: Path, updates: dict[str, Any]) -> Path:
    data = read_config_data(work_dir)
    data.update(updates)
    return save_config_data(work_dir, data)


def load_config(work_dir: Path) -> AppConfig:
    data = read_config_data(work_dir)
    api_id = int(data.get("api_id") or 0)
    api_hash = str(data.get("api_hash") or "")
    if not api_id or api_id == 1234567 or not api_hash or api_hash == "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx":
        raise ValueError("config.json does not contain valid Telegram API credentials")
    mode = _normalize_mode(data.get("operation_mode"))
    internal = dict(MODE_INTERNALS[mode])
    cfg = AppConfig(
        work_dir=work_dir,
        api_id=api_id,
        api_hash=api_hash,
        global_alert_channel_id=data.get("global_alert_channel_id"),
        notify_channel_id=data.get("notify_channel_id"),
        cmd_prefix=str(data.get("cmd_prefix") or "-"),
        service_name_prefix=_normalize_service_name(data.get("service_name_prefix")),
        operation_mode=mode,
        auto_sync_enabled=_normalize_bool(data.get("auto_sync_enabled"), True),
        auto_sync_time=_normalize_time_hhmm(data.get("auto_sync_time"), PUBLIC_DEFAULT_CONFIG["auto_sync_time"]),
        auto_route_enabled=_normalize_bool(data.get("auto_route_enabled"), True),
        auto_route_time=_normalize_time_hhmm(data.get("auto_route_time"), PUBLIC_DEFAULT_CONFIG["auto_route_time"]),
        panel_auto_delete_seconds=_normalize_non_negative_int(data.get("panel_auto_delete_seconds"), PUBLIC_DEFAULT_CONFIG["panel_auto_delete_seconds"]),
        notify_auto_delete_seconds=_normalize_non_negative_int(data.get("notify_auto_delete_seconds"), PUBLIC_DEFAULT_CONFIG["notify_auto_delete_seconds"]),
        recycle_fallback_command_seconds=_normalize_non_negative_int(data.get("recycle_fallback_command_seconds"), PUBLIC_DEFAULT_CONFIG["recycle_fallback_command_seconds"]),
        repo_url=data.get("repo_url") or None,
        plugins_repo_url=data.get("plugins_repo_url") or None,
        plugins_dir=str(data.get("plugins_dir") or PUBLIC_DEFAULT_CONFIG["plugins_dir"]),
        scheduler_poll_seconds=int(internal["scheduler_poll_seconds"]),
        snapshot_flush_debounce_seconds=int(internal["snapshot_flush_debounce_seconds"]),
        reload_debounce_seconds=float(internal["reload_debounce_seconds"]),
        manual_heavy_delay_seconds=float(internal["manual_heavy_delay_seconds"]),
        restart_delay_seconds=float(internal["restart_delay_seconds"]),
        update_delay_seconds=float(internal["update_delay_seconds"]),
        route_apply_delay_seconds=float(internal["route_apply_delay_seconds"]),
        max_parallel_admin_jobs=int(internal["max_parallel_admin_jobs"]),
        idle_grace_seconds=int(internal["idle_grace_seconds"]),
        daily_jitter_minutes=int(internal["daily_jitter_minutes"]),
        route_batch_size=int(internal["route_batch_size"]),
        sync_batch_size=int(internal["sync_batch_size"]),
        batch_sleep_min_seconds=float(internal["batch_sleep_min_seconds"]),
        batch_sleep_max_seconds=float(internal["batch_sleep_max_seconds"]),
        revision_poll_seconds=int(internal["revision_poll_seconds"]),
    )
    cfg.runtime_dir.mkdir(parents=True, exist_ok=True)
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    cfg.sessions_dir.mkdir(parents=True, exist_ok=True)
    cfg.backups_dir.mkdir(parents=True, exist_ok=True)
    cfg.plugins_root.mkdir(parents=True, exist_ok=True)
    return cfg


def sync_snapshot_to_config(work_dir: Path, db: object) -> None:
    data = read_config_data(work_dir)
    if hasattr(db, "export_legacy_snapshot"):
        snapshot = db.export_legacy_snapshot()
        data["folder_rules"] = snapshot.get("folder_rules", {})
        data["_system_cache"] = snapshot.get("_system_cache", {})
        data["auto_route_rules"] = snapshot.get("auto_route_rules", {})
    save_config_data(work_dir, data)
