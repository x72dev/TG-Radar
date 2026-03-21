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
from .db import RadarDB
from .logger import setup_logger
from .telegram_utils import build_message_link, blockquote_preview, bullet, escape, html_code, panel, section
from .version import __version__


@dataclass
class RuntimeState:
    target_map: dict[int, list[dict]]
    valid_rules_count: int
    revision: int
    started_at: datetime


@dataclass
class RuleHit:
    rule_name: str
    total_count: int
    first_hit: str


def severity_label(rule_count: int, total_hits: int) -> tuple[str, str]:
    if rule_count >= 3 or total_hits >= 8:
        return "高优先级", "🔥"
    if rule_count >= 2 or total_hits >= 4:
        return "高关注", "🚨"
    return "常规命中", "⚠️"


def collect_rule_hits(pattern: re.Pattern[str], text: str, max_collect: int = 20) -> tuple[int, str | None]:
    count = 0
    first_hit: str | None = None
    for idx, match in enumerate(pattern.finditer(text)):
        if idx >= max_collect:
            count += 1
            continue
        count += 1
        if first_hit is None:
            first_hit = match.group(0)
    return count, first_hit


def render_alert_message(
    *,
    folder_name: str,
    chat_title: str,
    sender_name: str,
    chat_id: int,
    msg_id: int,
    msg_link: str,
    msg_text: str,
    rule_hits: list[RuleHit],
) -> str:
    total_hits = sum(item.total_count for item in rule_hits)
    severity, icon = severity_label(len(rule_hits), total_hits)
    detail_rows: list[str] = []
    for item in rule_hits[:4]:
        detail_rows.append(f"· {escape(item.rule_name)}：{html_code(item.first_hit)} × {html_code(item.total_count)}")
    if len(rule_hits) > 4:
        detail_rows.append(f"· 其余规则：{html_code('+' + str(len(rule_hits) - 4))}")

    sections = [
        section(
            "告警摘要",
            [
                bullet("等级", severity),
                bullet("分组", folder_name),
                bullet("规则数", len(rule_hits)),
                bullet("累计词频", total_hits),
                bullet("时间", datetime.now().strftime("%m-%d %H:%M:%S")),
            ],
        ),
        section(
            "来源信息",
            [
                bullet("来源", chat_title),
                bullet("发送者", sender_name),
                bullet("Chat ID", chat_id),
                bullet("Message ID", msg_id),
            ],
        ),
        section("命中详情", detail_rows),
        section("消息预览", [blockquote_preview(msg_text, 880)]),
    ]
    footer = f"{icon} <a href=\"{msg_link}\">打开原始消息</a>" if msg_link else f"{icon} <i>当前消息不支持直达链接</i>"
    return panel("TG-Radar 告警通知", sections, footer)


def reload_runtime_state(db: RadarDB, alert_channel_id: int | None, logger, state: RuntimeState) -> RuntimeState:
    raw_target_map, valid_rules_count = db.build_target_map(alert_channel_id)
    compiled = compile_target_map(raw_target_map, logger)
    return RuntimeState(
        target_map=compiled,
        valid_rules_count=valid_rules_count,
        revision=db.get_revision(),
        started_at=state.started_at,
    )


async def run(work_dir: Path) -> None:
    config = load_config(work_dir)
    logger = setup_logger("tg-radar-core", config.logs_dir / "core.log")
    db = RadarDB(config.db_path)
    seed_db_from_legacy_config_if_needed(work_dir, db)
    config.sessions_dir.mkdir(parents=True, exist_ok=True)

    if not (config.core_session.with_suffix(".session")).exists():
        raise FileNotFoundError("Missing runtime/sessions/tg_radar_core.session. Run bootstrap_session.py first.")

    lock_file = work_dir / ".core.lock"
    lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
    try:
        if sys.platform != "win32":
            import fcntl
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        raise RuntimeError("tg-radar-core is already running")

    started_at = datetime.now()
    raw_target_map, valid_rules_count = db.build_target_map(config.global_alert_channel_id)
    state = RuntimeState(
        target_map=compile_target_map(raw_target_map, logger),
        valid_rules_count=valid_rules_count,
        revision=db.get_revision(),
        started_at=started_at,
    )

    stop_event = asyncio.Event()
    reload_event = asyncio.Event()

    async with TelegramClient(str(config.core_session), config.api_id, config.api_hash) as client:
        client.parse_mode = "html"
        logger.info("core service started, version=%s, revision=%s, chats=%s, rules=%s, reload=%s", __version__, state.revision, len(state.target_map), state.valid_rules_count, "signal" if hasattr(signal, "SIGUSR1") else "poll")
        db.log_event("INFO", "CORE", f"core service started v{__version__}")

        @client.on(events.NewMessage)
        async def message_handler(event: events.NewMessage.Event) -> None:
            try:
                if not (event.is_group or event.is_channel):
                    return
                tasks = state.target_map.get(int(event.chat_id))
                if not tasks:
                    return
                msg_text = event.raw_text or ""
                if not msg_text:
                    return

                chat = await event.get_chat()
                chat_title = getattr(chat, "title", None) or getattr(chat, "username", None) or "未知来源"
                try:
                    sender = await event.get_sender()
                    if getattr(sender, "bot", False):
                        return
                    sender_name = getattr(sender, "username", None) or getattr(sender, "first_name", None) or "隐藏用户"
                except Exception:
                    sender_name = "广播系统"

                msg_link = build_message_link(chat, int(event.chat_id), int(event.id))
                sent_routes: set[tuple[int, str]] = set()

                for task in tasks:
                    route_key = (int(task["alert_channel"]), str(task["folder_name"]))
                    if route_key in sent_routes:
                        continue

                    rule_hits: list[RuleHit] = []
                    for rule_name, pattern in task["rules"]:
                        count, first_hit = collect_rule_hits(pattern, msg_text)
                        if count <= 0 or not first_hit:
                            continue
                        rule_hits.append(RuleHit(rule_name=rule_name, total_count=count, first_hit=first_hit))

                    if not rule_hits:
                        continue

                    sent_routes.add(route_key)
                    alert_text = render_alert_message(
                        folder_name=str(task["folder_name"]),
                        chat_title=chat_title,
                        sender_name=sender_name,
                        chat_id=int(event.chat_id),
                        msg_id=int(event.id),
                        msg_link=msg_link,
                        msg_text=msg_text,
                        rule_hits=rule_hits,
                    )
                    try:
                        await client.send_message(int(task["alert_channel"]), alert_text, link_preview=False)
                        db.increment_hit(str(task["folder_name"]))
                        db.log_event("INFO", "HIT", f"{task['folder_name']} <- {chat_title} | rules={len(rule_hits)} hits={sum(item.total_count for item in rule_hits)}")
                    except Exception as exc:
                        logger.exception("failed to send alert: %s", exc)
                        db.log_event("ERROR", "SEND_ALERT", str(exc))
            except Exception as exc:
                logger.exception("message handler error: %s", exc)
                db.log_event("ERROR", "CORE_HANDLER", str(exc))

        async def perform_reload(trigger: str) -> None:
            nonlocal state
            state = reload_runtime_state(db, config.global_alert_channel_id, logger, state)
            logger.info("core reloaded trigger=%s revision=%s chats=%s rules=%s", trigger, state.revision, len(state.target_map), state.valid_rules_count)
            db.log_event("INFO", "CORE_RELOAD", f"trigger={trigger}; revision={state.revision}")

        async def signal_reload_watcher() -> None:
            while not stop_event.is_set():
                await reload_event.wait()
                reload_event.clear()
                try:
                    await perform_reload("signal")
                except Exception as exc:
                    logger.exception("signal reload error: %s", exc)
                    db.log_event("ERROR", "CORE_RELOAD", str(exc))

        async def revision_fallback_watcher() -> None:
            while not stop_event.is_set():
                try:
                    latest = db.get_revision()
                    if latest != state.revision:
                        await perform_reload("fallback_poll")
                except Exception as exc:
                    logger.exception("revision fallback watcher error: %s", exc)
                    db.log_event("ERROR", "CORE_WATCHER", str(exc))
                await asyncio.sleep(config.revision_poll_seconds)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass
        if hasattr(signal, "SIGUSR1"):
            try:
                loop.add_signal_handler(signal.SIGUSR1, reload_event.set)
            except NotImplementedError:
                pass

        background = [
            asyncio.create_task(signal_reload_watcher()),
            asyncio.create_task(client.run_until_disconnected()),
            asyncio.create_task(stop_event.wait()),
        ]
        if config.revision_poll_seconds > 0:
            background.append(asyncio.create_task(revision_fallback_watcher()))
        _done, pending = await asyncio.wait(set(background), return_when=asyncio.FIRST_COMPLETED)
        stop_event.set()
        reload_event.set()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        logger.info("core service stopping")
        db.log_event("INFO", "CORE", "core service stopping")


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
