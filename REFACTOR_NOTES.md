# TG-Radar v2 Refactor Notes

## Why the old design felt slow

The old design coupled these paths together:

1. command ingress
2. Saved Messages detection
3. SQLite writes and event logging
4. message editing/reply I/O
5. heavy task dispatch

That made `-help` vulnerable to the same stalls as `-sync`.

## What this refactor changes

### 1. Light path vs heavy path

- Light commands reply directly.
- Heavy commands enter the job bus and return an acceptance card.
- Final heavy results are sent as a new message, not by editing the original command.

### 2. PagerMaid-style modularization

Borrowed concepts from PagerMaid-Pyro:

- command registry / module registration
- service split for message I/O and formatting
- plugin-oriented command surface
- cleaner boundary between runtime wrapper and feature handlers

Implemented here as:

- `tgr/app/commands.py`
- `tgr/plugins/builtin_commands.py`
- `tgr/services/message_io.py`
- `tgr/services/formatters.py`

### 3. Startup lifecycle

Startup is reordered to support:

1. config/database bootstrap
2. optional folder snapshot bootstrap sync
3. config snapshot flush
4. startup notification
5. scheduler loop

### 4. Core reload model

The core watcher rebuilds from DB revision changes instead of depending on tight coupling with admin command execution.

## Important migration note

This package is a structural refactor. It preserves the public intent of the original TG-Radar command set, but it is not claimed to be byte-for-byte behavior identical to the upstream repo.
