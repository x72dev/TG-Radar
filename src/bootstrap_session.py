from __future__ import annotations
import asyncio
from pathlib import Path
from tgr.compat import seed_db_from_legacy_config_if_needed
from tgr.config import load_config, sync_snapshot_to_config
from tgr.db import RadarDB
from tgr.telegram_client_factory import build_telegram_client

async def main() -> None:
    wd = Path(__file__).resolve().parent.parent
    cfg = load_config(wd)
    db = RadarDB(cfg.db_path)
    seed_db_from_legacy_config_if_needed(wd, db)
    sync_snapshot_to_config(wd, db)
    cfg.sessions_dir.mkdir(parents=True, exist_ok=True)
    print("\n\033[1mTG-Radar · Telegram 授权\033[0m")
    print("─" * 50)
    print("请按提示输入手机号、验证码、二步验证密码（如有）。\n")
    async with build_telegram_client(cfg) as client:
        await client.start()
        me = await client.get_me()
        name = getattr(me, "username", None) or getattr(me, "first_name", "?")
        print(f"\033[32m✔\033[0m 已授权: {name}")
    print(f"\nSession 已写入: {cfg.session_path.with_suffix('.session')}")
    print("\n\033[32m✔\033[0m 授权完成。可以启动服务了。\n")

if __name__ == "__main__":
    asyncio.run(main())
