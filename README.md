# TG-Radar v2 Refactor

This package is a structural refactor of TG-Radar with a PagerMaid-style modular split.

## What changed

- Light commands (`-help`, `-ping`, `-status`, `-folders`, `-jobs`, `-log`) now reply directly.
- Heavy commands (`-sync`, `-routescan`, `-update`, `-restart`) are submitted to a job bus and reply asynchronously.
- The old "edit the original command message" interaction is no longer the default.
- Admin hot path is isolated from heavy jobs.
- Startup lifecycle is reordered to support bootstrap sync before startup notification.
- Command handling is registry/plugin based.
- Core watcher rebuilds monitoring snapshot from DB revision changes.

## Key folders

- `src/tgr/app` – command registry primitives
- `src/tgr/plugins` – builtin command modules
- `src/tgr/services` – message I/O and formatter services
- `src/tgr/admin_service.py` – admin runtime wrapper
- `src/tgr/core_service.py` – core runtime wrapper
- `src/tgr/sync_logic.py` – folder sync and route scan logic
- `src/tgr/db.py` – SQLite schema and repositories

## Notes

- `routescan` attempts to update Telegram folder filters directly.
- `core` sends alerts to folder-specific target channels, falling back to `global_alert_channel_id`.
- The package is syntax-checked in this environment, but not live-tested against your Telegram account/session.

## Bootstrap

```bash
python src/bootstrap_session.py
python src/sync_once.py
python src/radar_admin.py
python src/radar_core.py
```
