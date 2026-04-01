from __future__ import annotations
import asyncio
from pathlib import Path
from tgr.compat import seed_db_from_legacy_config_if_needed
from tgr.config import load_config, sync_snapshot_to_config
from tgr.db import RadarDB
from tgr.sync_logic import scan_auto_routes, sync_dialog_folders
from tgr.telegram_client_factory import build_telegram_client
async def main() -> None:
    wd = Path(__file__).resolve().parent.parent
    cfg = load_config(wd)
    db = RadarDB(cfg.db_path)
    seed_db_from_legacy_config_if_needed(wd, db)
    async with build_telegram_client(cfg) as client:
        sr = await sync_dialog_folders(client, db, cfg)
        rr = await scan_auto_routes(client, db, cfg)
        sync_snapshot_to_config(wd, db)
        print(f"✔ 同步完成 | 变动={sr.has_changes} 新增={len(sr.discovered)} 补群={sum(rr.queued.values())}")
if __name__ == "__main__":
    asyncio.run(main())
