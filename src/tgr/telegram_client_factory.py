from __future__ import annotations

import os
import platform

from telethon import TelegramClient

from .config import AppConfig
from .version import __version__


def _resolve_telethon_fingerprint() -> tuple[str, str, str]:
    """构造稳定、可覆盖的 Telethon 设备指纹，避免被识别为匿名服务端会话。"""
    system_default = f"{platform.system()} {platform.release()}".strip() or "Linux"
    device_model = (os.getenv("TG_DEVICE_MODEL") or "TG-Radar").strip()[:64]
    system_version = (os.getenv("TG_SYSTEM_VERSION") or system_default).strip()[:64]
    app_version = (os.getenv("TG_APP_VERSION") or f"TG-Radar/{__version__}").strip()[:32]
    return (
        device_model or "TG-Radar",
        system_version or "Linux",
        app_version or f"TG-Radar/{__version__}",
    )


def build_telegram_client(config: AppConfig) -> TelegramClient:
    device_model, system_version, app_version = _resolve_telethon_fingerprint()
    return TelegramClient(
        str(config.session_path),
        config.api_id,
        config.api_hash,
        device_model=device_model,
        system_version=system_version,
        app_version=app_version,
    )

