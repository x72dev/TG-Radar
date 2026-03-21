from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass, field
from typing import Any

from telethon import functions, types, utils

from .telegram_utils import dialog_filter_title


@dataclass(slots=True)
class SyncReport:
    discovered: list[str] = field(default_factory=list)
    active: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        total_chats = sum(self.active.values())
        return f"sync folders={len(self.active)} chats={total_chats} discovered={len(self.discovered)} errors={len(self.errors)}"


@dataclass(slots=True)
class RouteReport:
    created: list[str] = field(default_factory=list)
    queued: dict[str, int] = field(default_factory=dict)
    matched_zero: list[str] = field(default_factory=list)
    already_in: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        return f"routescan queued={sum(self.queued.values())} created={len(self.created)} errors={len(self.errors)}"


def _folder_title(raw: Any) -> str:
    return dialog_filter_title(raw).strip() or '未命名分组'


def _ensure_dialog_filter(filter_id: int, title: str) -> types.DialogFilter:
    return types.DialogFilter(
        id=filter_id,
        title=title,
        pinned_peers=[],
        include_peers=[],
        exclude_peers=[],
        contacts=False,
        non_contacts=False,
        groups=True,
        broadcasts=True,
        bots=False,
        exclude_muted=False,
        exclude_read=False,
        exclude_archived=False,
        emoticon='📡',
    )


async def sync_dialog_folders(client, db, config) -> SyncReport:
    report = SyncReport()
    try:
        filters = await client(functions.messages.GetDialogFiltersRequest())
    except Exception as exc:
        report.errors['filters'] = str(exc)
        return report

    folder_by_id: dict[int, str] = {}
    for raw in filters:
        if isinstance(raw, types.DialogFilter):
            title = _folder_title(raw)
            folder_by_id[int(raw.id)] = title
            db.upsert_folder(title, folder_id=int(raw.id), enabled=True)
            report.discovered.append(title)

    grouped: dict[int, list[tuple[int, str]]] = {fid: [] for fid in folder_by_id}
    batch = 0
    async for dialog in client.iter_dialogs(ignore_migrated=True):
        folder_id = getattr(dialog, 'folder_id', None)
        if folder_id is None or int(folder_id) not in folder_by_id:
            continue
        if not (getattr(dialog, 'is_group', False) or getattr(dialog, 'is_channel', False)):
            continue
        grouped[int(folder_id)].append((int(dialog.id), dialog.title or str(dialog.id)))
        batch += 1
        if batch % max(config.sync_batch_size, 1) == 0:
            lo, hi = config.sync_pause_seconds
            await asyncio.sleep(random.uniform(lo, hi))

    for folder_id, title in folder_by_id.items():
        items = grouped.get(folder_id, [])
        db.replace_folder_cache(title, items)
        report.active[title] = len(items)
    db.set_runtime_value('last_sync', db._now())
    return report


async def _upsert_filter_peer(client, filter_row, peer) -> None:
    filters = await client(functions.messages.GetDialogFiltersRequest())
    target = None
    for raw in filters:
        if isinstance(raw, types.DialogFilter) and int(raw.id) == int(filter_row['folder_id']):
            target = raw
            break
    if target is None:
        target = _ensure_dialog_filter(int(filter_row['folder_id']), str(filter_row['folder_name']))
    include_peers = list(getattr(target, 'include_peers', []) or [])
    if all(utils.get_peer_id(p) != utils.get_peer_id(peer) for p in include_peers):
        include_peers.append(peer)
    patched = types.DialogFilter(
        id=int(target.id),
        title=_folder_title(target),
        pinned_peers=list(getattr(target, 'pinned_peers', []) or []),
        include_peers=include_peers,
        exclude_peers=list(getattr(target, 'exclude_peers', []) or []),
        contacts=bool(getattr(target, 'contacts', False)),
        non_contacts=bool(getattr(target, 'non_contacts', False)),
        groups=bool(getattr(target, 'groups', True)),
        broadcasts=bool(getattr(target, 'broadcasts', True)),
        bots=bool(getattr(target, 'bots', False)),
        exclude_muted=bool(getattr(target, 'exclude_muted', False)),
        exclude_read=bool(getattr(target, 'exclude_read', False)),
        exclude_archived=bool(getattr(target, 'exclude_archived', False)),
        emoticon=getattr(target, 'emoticon', '📡'),
    )
    await client(functions.messages.UpdateDialogFilterRequest(id=int(patched.id), filter=patched))


async def scan_auto_routes(client, db, config) -> RouteReport:
    report = RouteReport()
    routes = db.list_routes()
    if not routes:
        return report

    folder_rows = {str(row['folder_name']): row for row in db.list_folders()}
    all_existing = db.list_all_cache_chat_ids()
    dialogs: list[Any] = []
    async for dialog in client.iter_dialogs(ignore_migrated=True):
        if getattr(dialog, 'is_group', False) or getattr(dialog, 'is_channel', False):
            dialogs.append(dialog)

    for route in routes:
        folder_name = str(route['folder_name'])
        pattern = str(route['pattern'])
        row = folder_rows.get(folder_name)
        if row is None:
            report.errors[folder_name] = '目标分组不存在'
            continue
        regex = re.compile(pattern, re.IGNORECASE)
        matched = 0
        already = 0
        queued = 0
        for dialog in dialogs:
            title = dialog.title or ''
            if not regex.search(title):
                continue
            matched += 1
            if int(dialog.id) in {int(r['chat_id']) for r in db.list_folder_cache(folder_name)}:
                already += 1
                continue
            try:
                peer = await client.get_input_entity(dialog.entity)
                await _upsert_filter_peer(client, row, peer)
                db.upsert_folder_cache(folder_name, int(dialog.id), title or str(dialog.id))
                db.add_route_task(folder_name, int(dialog.id), title or str(dialog.id), 'added_to_filter', status='done')
                queued += 1
            except Exception as exc:
                db.add_route_task(folder_name, int(dialog.id), title or str(dialog.id), str(exc), status='failed')
                report.errors[f'{folder_name}:{dialog.id}'] = str(exc)
        if matched == 0:
            report.matched_zero.append(folder_name)
        if already:
            report.already_in[folder_name] = already
        if queued:
            report.queued[folder_name] = queued
    db.set_runtime_value('last_route_scan', db._now())
    return report
