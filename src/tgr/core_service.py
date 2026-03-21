from __future__ import annotations

import asyncio
import re
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from telethon import TelegramClient, events, utils

from .compat import seed_db_from_legacy_config_if_needed
from .config import load_config
from .db import RadarDB
from .logger import setup_logger
from .telegram_utils import blockquote_preview, bullet, escape, panel, section
from .version import __version__


@dataclass(slots=True)
class CompiledRule:
    folder_name: str
    rule_name: str
    target_id: int
    regex: re.Pattern[str]


class CoreApp:
    def __init__(self, work_dir: Path) -> None:
        self.config = load_config(work_dir)
        self.logger = setup_logger('tg-radar-core', self.config.logs_dir / 'core.log')
        self.db = RadarDB(self.config.db_path)
        seed_db_from_legacy_config_if_needed(work_dir, self.db)
        self.stop_event = asyncio.Event()
        self.client: TelegramClient | None = None
        self.snapshot: dict[int, list[CompiledRule]] = {}
        self.revision = -1

    def rebuild_snapshot(self) -> None:
        raw = self.db.build_monitor_snapshot(self.config.global_alert_channel_id)
        snapshot: dict[int, list[CompiledRule]] = {}
        for chat_id, rules in raw.items():
            bucket = []
            for item in rules:
                try:
                    bucket.append(CompiledRule(
                        folder_name=str(item['folder_name']),
                        rule_name=str(item['rule_name']),
                        target_id=int(item['target_id']),
                        regex=re.compile(str(item['pattern']), re.IGNORECASE),
                    ))
                except re.error as exc:
                    self.db.try_log_event('ERROR', 'RULE_COMPILE', f"{item['folder_name']}/{item['rule_name']}: {exc}")
            if bucket:
                snapshot[int(chat_id)] = bucket
        self.snapshot = snapshot
        self.revision = self.db.get_revision()
        self.db.mark_core_reloaded()
        self.logger.info('core snapshot rebuilt chats=%s revision=%s', len(snapshot), self.revision)

    async def watch_revision(self) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(2.0)
            current = self.db.get_revision()
            if current != self.revision:
                self.rebuild_snapshot()

    async def handle_message(self, event: events.NewMessage.Event) -> None:
        chat_id = int(getattr(event, 'chat_id', 0) or 0)
        rules = self.snapshot.get(chat_id)
        if not rules:
            return
        text = (event.raw_text or '').strip()
        if not text:
            return
        for rule in rules:
            if not rule.regex.search(text):
                continue
            try:
                await self.send_alert(event, rule)
                self.db.try_log_event('INFO', 'ALERT', f"{rule.folder_name}/{rule.rule_name} chat={chat_id}")
            except Exception as exc:
                self.logger.warning('send alert failed: %s', exc)
            break

    async def send_alert(self, event: events.NewMessage.Event, rule: CompiledRule) -> None:
        assert self.client is not None
        link = ''
        try:
            entity = await event.get_chat()
            username = getattr(entity, 'username', None)
            if username:
                link = f'https://t.me/{username}/{event.id}'
        except Exception:
            link = ''
        text = panel(
            'TG-Radar 告警',
            [
                section('命中信息', [
                    bullet('分组', rule.folder_name, code=False),
                    bullet('规则', rule.rule_name, code=False),
                    bullet('chat_id', event.chat_id),
                    bullet('message_id', event.id),
                ]),
                section('消息预览', [blockquote_preview(event.raw_text or '', 700)]),
            ],
            f'<i>{escape(link) if link else "无公开链接"}</i>',
        )
        await self.client.send_message(rule.target_id, text, parse_mode='html')

    async def run(self) -> None:
        self.config.ensure_runtime_dirs()
        core_session = self.config.core_session.with_suffix('.session')
        admin_session = self.config.admin_session.with_suffix('.session')
        if not core_session.exists() and admin_session.exists():
            core_session.write_bytes(admin_session.read_bytes())
        if not core_session.exists():
            raise FileNotFoundError('Missing runtime/sessions/tg_radar_core.session. Run bootstrap_session.py first.')
        async with TelegramClient(str(self.config.core_session), self.config.api_id, self.config.api_hash) as client:
            self.client = client
            client.parse_mode = 'html'
            self.rebuild_snapshot()

            @client.on(events.NewMessage(incoming=True))
            async def on_message(event: events.NewMessage.Event) -> None:
                await self.handle_message(event)

            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, self.stop_event.set)
                except NotImplementedError:
                    pass
            tasks = [
                asyncio.create_task(client.run_until_disconnected()),
                asyncio.create_task(self.watch_revision()),
                asyncio.create_task(self.stop_event.wait()),
            ]
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            self.stop_event.set()
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


async def run(work_dir: Path) -> None:
    app = CoreApp(work_dir)
    await app.run()
