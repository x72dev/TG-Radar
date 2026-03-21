from __future__ import annotations

import asyncio
from pathlib import Path

from telethon import TelegramClient

from tgr.compat import seed_db_from_legacy_config_if_needed
from tgr.config import load_config, sync_snapshot_to_config
from tgr.db import RadarDB
from tgr.sync_logic import scan_auto_routes, sync_dialog_folders


async def main() -> None:
    work_dir = Path(__file__).resolve().parent.parent
    config = load_config(work_dir)
    db = RadarDB(config.db_path)
    seed_db_from_legacy_config_if_needed(work_dir, db)
    async with TelegramClient(str(config.admin_session), config.api_id, config.api_hash) as client:
        sync_report = await sync_dialog_folders(client, db, config)
        route_report = await scan_auto_routes(client, db, config)
        sync_snapshot_to_config(work_dir, db)
        print(
            "TR 管理器 同步完成 | "
            f"changed={sync_report.has_changes} | "
            f"discovered={len(sync_report.discovered)} | "
            f"renamed={len(sync_report.renamed)} | "
            f"deleted={len(sync_report.deleted)} | "
            f"queued={sum(route_report.queued.values())}"
        )


if __name__ == "__main__":
    asyncio.run(main())
