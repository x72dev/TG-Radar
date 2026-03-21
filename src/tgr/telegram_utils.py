from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from telethon import types, utils

REGEX_HINT_RE = re.compile(r"[\\\(\)\[\]\{\}|.+?^$]")
SPLIT_COMMAS_RE = re.compile(r"[,，]+")


def escape(value: object) -> str:
    return html.escape(str(value))


def html_code(text: object) -> str:
    return f"<code>{escape(text)}</code>"


def resolve_peer_id(peer: object) -> int:
    """Return a consistent marked peer ID (negative for channels/chats)."""
    try:
        return int(utils.get_peer_id(peer, add_mark=True))
    except Exception:
        return 0


def dialog_filter_title(folder: types.DialogFilter) -> str:
    raw = folder.title
    return raw.text if hasattr(raw, "text") else str(raw)


def build_message_link(chat: object, chat_id: int, msg_id: int) -> str:
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    try:
        real_id, peer_type = utils.resolve_id(int(chat_id))
        if peer_type == types.PeerChannel:
            return f"https://t.me/c/{real_id}/{msg_id}"
    except Exception:
        pass
    return ""


def format_duration(seconds: float) -> str:
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    return " ".join(parts) or "不足1分钟"


def _token_is_regex(token: str) -> bool:
    return bool(REGEX_HINT_RE.search(token))


def split_terms(raw: str | Sequence[str]) -> list[str]:
    if isinstance(raw, str):
        source = [p.strip() for p in raw.split() if p.strip()]
    else:
        source = [str(p).strip() for p in raw if str(p).strip()]
    parts: list[str] = []
    for item in source:
        if not item:
            continue
        if _token_is_regex(item):
            parts.append(item)
            continue
        pieces = [x.strip() for x in SPLIT_COMMAS_RE.split(item) if x.strip()]
        parts.extend(pieces or [item])
    return parts


def _normalize_token(token: str) -> str:
    token = token.strip()
    if not token:
        return ""
    return token if _token_is_regex(token) else re.escape(token)


def normalize_pattern_from_terms(raw: str | Sequence[str]) -> str:
    parts = split_terms(raw)
    if not parts:
        raise ValueError("empty terms")
    normalized = [x for x in (_normalize_token(part) for part in parts) if x]
    if not normalized:
        raise ValueError("empty pattern")
    normalized = list(dict.fromkeys(normalized))
    if len(normalized) == 1:
        return normalized[0]
    return "(" + "|".join(normalized) + ")"


def merge_patterns(existing: str | None, incoming: str) -> str:
    existing = (existing or "").strip()
    incoming = (incoming or "").strip()
    if not existing:
        return incoming
    if existing == incoming:
        return existing
    old_inner = existing[1:-1] if existing.startswith("(") and existing.endswith(")") else existing
    new_inner = incoming[1:-1] if incoming.startswith("(") and incoming.endswith(")") else incoming
    tokens = [t.strip() for t in re.split(r"(?<!\\)\|", old_inner) if t.strip()]
    tokens.extend(t.strip() for t in re.split(r"(?<!\\)\|", new_inner) if t.strip())
    tokens = list(dict.fromkeys(tokens))
    if len(tokens) == 1:
        return tokens[0]
    return "(" + "|".join(tokens) + ")"


def try_remove_terms_from_pattern(pattern: str, terms: Iterable[str]) -> str | None:
    pattern = pattern.strip()
    if not pattern:
        return None
    inner = pattern[1:-1] if pattern.startswith("(") and pattern.endswith(")") else pattern
    tokens = [t.strip() for t in re.split(r"(?<!\\)\|", inner) if t.strip()]
    cleaned = set(split_terms(list(terms)))
    escaped = {re.escape(x) for x in cleaned}
    left = [token for token in tokens if token not in cleaned and token not in escaped and html.unescape(token) not in cleaned]
    if not left:
        return None
    if len(left) == 1:
        return left[0]
    return "(" + "|".join(left) + ")"


def truncate_for_panel(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def compact_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def blockquote_preview(text: str, limit: int = 900) -> str:
    return f"<blockquote expandable>{escape(truncate_for_panel(compact_text(text), limit))}</blockquote>"


def bullet(label: str, value: object | None = None, *, code: bool = True, prefix: str = "·") -> str:
    if value is None:
        return f"{prefix} {escape(label)}"
    rendered = html_code(value) if code else escape(value)
    return f"{prefix} {escape(label)}：{rendered}"


def soft_kv(label: str, value: object | None = None) -> str:
    if value is None:
        return f"· {escape(label)}"
    return f"· {escape(label)}：{escape(value)}"


def section(title: str, rows: Sequence[str]) -> str:
    rows = [row for row in rows if row]
    if not rows:
        return ""
    return f"<b>{escape(title)}</b>\n" + "\n".join(rows)


def panel(title: str, sections: Sequence[str], footer: str | None = None) -> str:
    body = [f"<b>{escape(title)}</b>"]
    for sec in sections:
        sec = sec.strip()
        if sec:
            body.append(sec)
    if footer:
        body.append(footer.strip())
    return "\n\n".join(body)


def shorten_path(path: object, keep: int = 2) -> str:
    parts = str(path).split("/")
    if len(parts) <= keep + 1:
        return str(path)
    return "…/" + "/".join(parts[-keep:])


# ---------------------------------------------------------------------------
# Alert rendering helpers (moved from core_service.py for proper decoupling)
# ---------------------------------------------------------------------------


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


def display_sender_name(sender: object | None, fallback: str = "未知用户") -> str:
    if sender is None:
        return fallback
    username = getattr(sender, "username", None)
    if username:
        username = str(username).lstrip("@")
        return f"@{username}"
    first_name = (getattr(sender, "first_name", None) or "").strip()
    last_name = (getattr(sender, "last_name", None) or "").strip()
    full = (first_name + (" " + last_name if last_name else "")).strip()
    return full or fallback


def render_alert_message(*, folder_name: str, chat_title: str, sender_name: str, msg_link: str, msg_text: str, rule_hits: list[RuleHit]) -> str:
    total_hits = sum(item.total_count for item in rule_hits)
    sev, icon = severity_label(len(rule_hits), total_hits)
    detail_rows: list[str] = []
    for item in rule_hits[:4]:
        detail_rows.append(f"· {escape(item.rule_name)}：{html_code(item.first_hit)} × {html_code(item.total_count)}")
    if len(rule_hits) > 4:
        detail_rows.append(f"· 其余规则：{html_code('+' + str(len(rule_hits) - 4))}")
    sections_list = [
        section("命中摘要", [bullet("等级", sev), bullet("分组", folder_name), bullet("来源", chat_title, code=False), bullet("发送者", sender_name, code=False), bullet("时间", datetime.now().strftime("%m-%d %H:%M:%S"), code=False)]),
        section("命中详情", detail_rows),
        section("消息预览", [blockquote_preview(msg_text, 760)]),
    ]
    footer = f'{icon} <a href="{msg_link}">打开原始消息</a>' if msg_link else f'{icon} <i>当前消息不支持直达链接</i>'
    return panel("TR 管理器 · 命中告警", sections_list, footer)
