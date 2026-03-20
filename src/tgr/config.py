from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "api_id": 1234567,
    "api_hash": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "global_alert_channel_id": None,
    "notify_channel_id": None,
    "cmd_prefix": "-",
    "service_name_prefix": "tg-radar",
    "sync_interval_seconds": 1800,
    "route_worker_interval_seconds": 4,
    "revision_poll_seconds": 3,
    "panel_auto_delete_seconds": 45,
    "notify_auto_delete_seconds": 0,
    "recycle_fallback_command_seconds": 8,
    "repo_url": "https://github.com/chenmo8848/TG-Radar.git",
    "scheduler_poll_seconds": 1,
    "snapshot_flush_debounce_seconds": 3,
    "max_parallel_admin_jobs": 2,
    "route_scan_interval_seconds": 120,
    "sync_auto_jitter_seconds": 8,
    "auto_route_rules": {},
    "folder_rules": {},
    "_system_cache": {},
}


@dataclass(frozen=True)
class AppConfig:
    work_dir: Path
    api_id: int
    api_hash: str
    global_alert_channel_id: int | None
    notify_channel_id: int | None
    cmd_prefix: str
    service_name_prefix: str
    sync_interval_seconds: int
    route_worker_interval_seconds: int
    revision_poll_seconds: int
    panel_auto_delete_seconds: int
    notify_auto_delete_seconds: int
    recycle_fallback_command_seconds: int
    repo_url: str | None
    scheduler_poll_seconds: int
    snapshot_flush_debounce_seconds: int
    max_parallel_admin_jobs: int
    route_scan_interval_seconds: int
    sync_auto_jitter_seconds: int

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
    def admin_session(self) -> Path:
        return self.sessions_dir / "tg_radar_admin"

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


def _normalize_positive_int(value: Any, default: int, minimum: int = 1) -> int:
    try:
        normalized = int(str(value).strip())
    except Exception:
        normalized = default
    return max(minimum, normalized)


def read_config_data(work_dir: Path) -> dict[str, Any]:
    path = work_dir / "config.json"
    raw: dict[str, Any]
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = {}

    # 清理旧版写入的中文说明字段与伪注释字段。
    raw = {k: v for k, v in raw.items() if not (k.startswith("_说明_") or k.startswith("_comment_"))}

    data = dict(DEFAULT_CONFIG)
    data.update(raw)
    data["api_id"] = int(data.get("api_id") or 0)
    data["api_hash"] = str(data.get("api_hash") or "")
    data["global_alert_channel_id"] = _normalize_int(data.get("global_alert_channel_id"))
    data["notify_channel_id"] = _normalize_int(data.get("notify_channel_id"))
    data["cmd_prefix"] = str(data.get("cmd_prefix") or "-")
    data["service_name_prefix"] = str(data.get("service_name_prefix") or "tg-radar")
    data["sync_interval_seconds"] = _normalize_positive_int(data.get("sync_interval_seconds"), DEFAULT_CONFIG["sync_interval_seconds"], 10)
    data["route_worker_interval_seconds"] = _normalize_positive_int(data.get("route_worker_interval_seconds"), DEFAULT_CONFIG["route_worker_interval_seconds"], 1)
    data["revision_poll_seconds"] = _normalize_positive_int(data.get("revision_poll_seconds"), DEFAULT_CONFIG["revision_poll_seconds"], 1)
    data["panel_auto_delete_seconds"] = _normalize_positive_int(data.get("panel_auto_delete_seconds"), DEFAULT_CONFIG["panel_auto_delete_seconds"], 0)
    data["notify_auto_delete_seconds"] = _normalize_positive_int(data.get("notify_auto_delete_seconds"), DEFAULT_CONFIG["notify_auto_delete_seconds"], 0)
    data["recycle_fallback_command_seconds"] = _normalize_positive_int(data.get("recycle_fallback_command_seconds"), DEFAULT_CONFIG["recycle_fallback_command_seconds"], 0)
    data["repo_url"] = str(data.get("repo_url") or DEFAULT_CONFIG["repo_url"])
    data["scheduler_poll_seconds"] = _normalize_positive_int(data.get("scheduler_poll_seconds"), DEFAULT_CONFIG["scheduler_poll_seconds"], 1)
    data["snapshot_flush_debounce_seconds"] = _normalize_positive_int(data.get("snapshot_flush_debounce_seconds"), DEFAULT_CONFIG["snapshot_flush_debounce_seconds"], 1)
    data["max_parallel_admin_jobs"] = _normalize_positive_int(data.get("max_parallel_admin_jobs"), DEFAULT_CONFIG["max_parallel_admin_jobs"], 1)
    data["route_scan_interval_seconds"] = _normalize_positive_int(data.get("route_scan_interval_seconds"), DEFAULT_CONFIG["route_scan_interval_seconds"], 10)
    data["sync_auto_jitter_seconds"] = _normalize_positive_int(data.get("sync_auto_jitter_seconds"), DEFAULT_CONFIG["sync_auto_jitter_seconds"], 0)
    data["auto_route_rules"] = data.get("auto_route_rules") or {}
    data["folder_rules"] = data.get("folder_rules") or {}
    data["_system_cache"] = data.get("_system_cache") or {}
    return data


def save_config_data(work_dir: Path, data: dict[str, Any]) -> Path:
    config_path = work_dir / "config.json"
    normalized = dict(DEFAULT_CONFIG)
    normalized.update(data)
    payload = {k: normalized[k] for k in DEFAULT_CONFIG.keys()}
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

    cfg = AppConfig(
        work_dir=work_dir,
        api_id=api_id,
        api_hash=api_hash,
        global_alert_channel_id=data.get("global_alert_channel_id"),
        notify_channel_id=data.get("notify_channel_id"),
        cmd_prefix=str(data.get("cmd_prefix") or "-"),
        service_name_prefix=str(data.get("service_name_prefix") or "tg-radar"),
        sync_interval_seconds=_normalize_positive_int(data.get("sync_interval_seconds"), DEFAULT_CONFIG["sync_interval_seconds"], 10),
        route_worker_interval_seconds=_normalize_positive_int(data.get("route_worker_interval_seconds"), DEFAULT_CONFIG["route_worker_interval_seconds"], 1),
        revision_poll_seconds=_normalize_positive_int(data.get("revision_poll_seconds"), DEFAULT_CONFIG["revision_poll_seconds"], 1),
        panel_auto_delete_seconds=_normalize_positive_int(data.get("panel_auto_delete_seconds"), DEFAULT_CONFIG["panel_auto_delete_seconds"], 0),
        notify_auto_delete_seconds=_normalize_positive_int(data.get("notify_auto_delete_seconds"), DEFAULT_CONFIG["notify_auto_delete_seconds"], 0),
        recycle_fallback_command_seconds=_normalize_positive_int(data.get("recycle_fallback_command_seconds"), DEFAULT_CONFIG["recycle_fallback_command_seconds"], 0),
        repo_url=data.get("repo_url") or None,
        scheduler_poll_seconds=_normalize_positive_int(data.get("scheduler_poll_seconds"), DEFAULT_CONFIG["scheduler_poll_seconds"], 1),
        snapshot_flush_debounce_seconds=_normalize_positive_int(data.get("snapshot_flush_debounce_seconds"), DEFAULT_CONFIG["snapshot_flush_debounce_seconds"], 1),
        max_parallel_admin_jobs=_normalize_positive_int(data.get("max_parallel_admin_jobs"), DEFAULT_CONFIG["max_parallel_admin_jobs"], 1),
        route_scan_interval_seconds=_normalize_positive_int(data.get("route_scan_interval_seconds"), DEFAULT_CONFIG["route_scan_interval_seconds"], 10),
        sync_auto_jitter_seconds=_normalize_positive_int(data.get("sync_auto_jitter_seconds"), DEFAULT_CONFIG["sync_auto_jitter_seconds"], 0),
    )
    cfg.runtime_dir.mkdir(parents=True, exist_ok=True)
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    cfg.sessions_dir.mkdir(parents=True, exist_ok=True)
    cfg.backups_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def sync_snapshot_to_config(work_dir: Path, db: object) -> None:
    data = read_config_data(work_dir)
    if hasattr(db, "export_legacy_snapshot"):
        snapshot = db.export_legacy_snapshot()
        data["folder_rules"] = snapshot.get("folder_rules", {})
        data["_system_cache"] = snapshot.get("_system_cache", {})
        data["auto_route_rules"] = snapshot.get("auto_route_rules", {})
    save_config_data(work_dir, data)
