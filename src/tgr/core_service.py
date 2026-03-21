from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient, events

from .compat import seed_db_from_legacy_config_if_needed
from .config import load_config
from .core.plugin_system import PluginManager
from .db import RadarDB
from .logger import setup_logger
from .version import __version__


@dataclass
class RuntimeState:
    target_map: dict[int, list[dict]]
    valid_rules_count: int
    revision: int
    started_at: datetime


def compile_target_map(raw_target_map: dict[int, list[dict]], logger) -> dict[int, list[dict]]:
    compiled: dict[int, list[dict]] = {}
    for chat_id, tasks in raw_target_map.items():
        for task in tasks:
            compiled_rules: list[tuple[str, re.Pattern[str]]] = []
            for rule_name, pattern in task["rules"]:
                try:
                    compiled_rules.append((rule_name, re.compile(pattern, re.IGNORECASE)))
                except re.error as exc:
                    logger.warning("invalid regex skipped: folder=%s rule=%s err=%s", task["folder_name"], rule_name, exc)
            if not compiled_rules:
                continue
            compiled.setdefault(chat_id, []).append({"folder_name": task["folder_name"], "alert_channel": task["alert_channel"], "rules": compiled_rules})
    return compiled


class CoreApp:
    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.config = load_config(work_dir)
        self.logger = setup_logger("tr-manager-core", self.config.logs_dir / "core.log")
        self.db = RadarDB(self.config.db_path)
        seed_db_from_legacy_config_if_needed(work_dir, self.db)
        self.stop_event = asyncio.Event()
        self.reload_event = asyncio.Event()
        self.plugin_manager = PluginManager(self)
        self.client: TelegramClient | None = None
        self.state: RuntimeState | None = None

    async def reload_runtime_state(self) -> RuntimeState:
        raw_target_map, valid_rules_count = self.db.build_target_map(self.config.global_alert_channel_id)
        compiled = compile_target_map(raw_target_map, self.logger)
        previous = self.state.started_at if self.state else datetime.now()
        self.state = RuntimeState(target_map=compiled, valid_rules_count=valid_rules_count, revision=self.db.get_revision(), started_at=previous)
        return self.state

    async def run(self) -> None:
        self.config.sessions_dir.mkdir(parents=True, exist_ok=True)
        if not (self.config.core_session.with_suffix(".session")).exists():
            raise FileNotFoundError("Missing runtime/sessions/tg_radar_core.session. Run bootstrap_session.py first.")
        lock_file = self.work_dir / ".core.lock"
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
        try:
            if sys.platform != "win32":
                import fcntl
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception as exc:
            raise RuntimeError("tg-radar-core is already running") from exc

        # Write PID file for reload signal targeting
        pid_file = self.config.runtime_dir / "core.pid"
        pid_file.write_text(str(os.getpid()))

        await self.reload_runtime_state()
        self.plugin_manager.load_core_plugins()
        await self.plugin_manager.run_healthchecks()
        async with TelegramClient(str(self.config.core_session), self.config.api_id, self.config.api_hash) as client:
            self.client = client
            client.parse_mode = "html"
            self.logger.info("core service started, version=%s, revision=%s, chats=%s, rules=%s", __version__, self.state.revision, len(self.state.target_map), self.state.valid_rules_count)
            self.db.log_event("INFO", "CORE", f"TR 管理器 Core 已启动 v{__version__}")

            @client.on(events.NewMessage)
            async def message_handler(event: events.NewMessage.Event) -> None:
                try:
                    await self.plugin_manager.process_core_message(self, event)
                except Exception as exc:
                    self.logger.exception("message handler error: %s", exc)
                    self.db.log_event("ERROR", "CORE_HANDLER", str(exc))

            async def perform_reload(trigger: str) -> None:
                await self.reload_runtime_state()
                self.logger.info("core reloaded trigger=%s revision=%s chats=%s rules=%s", trigger, self.state.revision, len(self.state.target_map), self.state.valid_rules_count)
                await self.plugin_manager.run_healthchecks()
                self.db.log_event("INFO", "CORE_RELOAD", f"trigger={trigger}; revision={self.state.revision}")

            async def signal_reload_watcher() -> None:
                while not self.stop_event.is_set():
                    await self.reload_event.wait()
                    self.reload_event.clear()
                    try:
                        await perform_reload("signal")
                    except Exception as exc:
                        self.logger.exception("signal reload error: %s", exc)
                        self.db.log_event("ERROR", "CORE_RELOAD", str(exc))

            async def revision_fallback_watcher() -> None:
                poll_interval = self.config.revision_poll_seconds
                while not self.stop_event.is_set():
                    try:
                        latest = self.db.get_revision()
                        if latest != self.state.revision:
                            await perform_reload("fallback_poll")
                    except Exception as exc:
                        self.logger.exception("revision fallback watcher error: %s", exc)
                        self.db.log_event("ERROR", "CORE_WATCHER", str(exc))
                    await asyncio.sleep(poll_interval)

            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, self.stop_event.set)
                except NotImplementedError:
                    pass
            if hasattr(signal, "SIGUSR1"):
                try:
                    loop.add_signal_handler(signal.SIGUSR1, self.reload_event.set)
                except NotImplementedError:
                    pass

            background = [
                asyncio.create_task(signal_reload_watcher()),
                asyncio.create_task(client.run_until_disconnected()),
                asyncio.create_task(self.stop_event.wait()),
            ]
            # FIX BUG-05: only start fallback watcher if revision_poll_seconds > 0
            if self.config.revision_poll_seconds > 0:
                background.append(asyncio.create_task(revision_fallback_watcher()))
            _done, pending = await asyncio.wait(set(background), return_when=asyncio.FIRST_COMPLETED)
            self.stop_event.set()
            self.reload_event.set()
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            self.logger.info("TR 管理器 Core stopping")
            self.db.log_event("INFO", "CORE", "TR 管理器 Core 正在停止")

            # Clean PID file
            try:
                pid_file.unlink(missing_ok=True)
            except Exception:
                pass


async def run(work_dir: Path) -> None:
    app = CoreApp(work_dir)
    await app.run()
