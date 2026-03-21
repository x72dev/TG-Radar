from __future__ import annotations

import asyncio
from telethon import TelegramClient


class MessageIO:
    def __init__(self, client: TelegramClient, *, panel_auto_delete_seconds: int) -> None:
        self.client = client
        self.panel_auto_delete_seconds = panel_auto_delete_seconds
        self._bg_tasks: set[asyncio.Task] = set()

    async def reply(self, event, text: str, *, auto_delete: int | None = None) -> None:
        msg = await self.client.send_message('me', text, reply_to=event.id)
        if auto_delete and auto_delete > 0:
            self._spawn(self._delete_later(msg, auto_delete))

    async def notify(self, text: str, *, auto_delete: int | None = None) -> None:
        msg = await self.client.send_message('me', text)
        if auto_delete and auto_delete > 0:
            self._spawn(self._delete_later(msg, auto_delete))

    async def update_or_reply(self, message_id: int | None, text: str, *, reply_to: int | None = None, auto_delete: int | None = None) -> None:
        if message_id:
            try:
                await self.client.edit_message('me', message_id, text)
                return
            except Exception:
                pass
        msg = await self.client.send_message('me', text, reply_to=reply_to)
        if auto_delete and auto_delete > 0:
            self._spawn(self._delete_later(msg, auto_delete))

    async def _delete_later(self, msg, delay: int) -> None:
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except Exception:
            pass

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
