import os, sys, json, asyncio, logging, subprocess, html
from datetime import datetime
from typing import Optional
from telethon import TelegramClient, functions, types, utils

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

WORK_DIR     = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.path.join(WORK_DIR, "config.json")
SESSION_NAME = os.path.join(WORK_DIR, "TG_Radar_session")
SERVICE_NAME = "tg_monitor"
VERSION = ""

def resolve_peer_id(peer) -> int:
    try:
        raw_id = utils.get_peer_id(peer)
        if isinstance(peer, (types.PeerChannel, types.PeerChat)):
            str_id = str(raw_id)
            if not str_id.startswith("-100") and not str_id.startswith("-"):
                return int(f"-100{str_id}")
        return raw_id
    except Exception: return 0

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f: return json.load(f)
    except Exception as e:
        logger.error("配置文件解析发生致命异常: %s", e)
        sys.exit(1)

def save_config(config_data: dict) -> None:
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4, ensure_ascii=False)
    os.replace(tmp_path, CONFIG_PATH)

def service_stop() -> None: subprocess.run(["sudo", "systemctl", "stop", SERVICE_NAME], stderr=subprocess.DEVNULL)
def service_start() -> None:
    open(os.path.join(WORK_DIR, ".silent_start"), "w").close()
    subprocess.run(["sudo", "systemctl", "start", SERVICE_NAME], stderr=subprocess.DEVNULL)

def get_folder_title(folder: types.DialogFilter) -> str:
    raw = folder.title
    return raw.text if hasattr(raw, "text") else str(raw)

def configs_differ(old: dict, new: dict) -> bool:
    return json.dumps(old, sort_keys=True, ensure_ascii=False) != json.dumps(new, sort_keys=True, ensure_ascii=False)

async def send_sync_report(client, notify_channel, report, elapsed, cmd_prefix):
    discovered, renamed, deleted, active, changed = report.get("discovered", []), report.get("renamed", []), report.get("deleted", []), report.get("active", {}), report.get("has_changes", False)
    change_lines = []
    for name in discovered: change_lines.append(f"  ✨ 新发现 <code>{html.escape(name)}</code>")
    for old_name, new_name in renamed: change_lines.append(f"  🔄 路由重定向 <code>{html.escape(old_name)}</code> → <code>{html.escape(new_name)}</code>")
    for name in deleted: change_lines.append(f"  🗑️ 废弃剔除 <code>{html.escape(name)}</code>")
    change_block = "\n".join(change_lines) if change_lines else "  _(无拓扑结构变更)_"
    active_lines = [f"  ✅ <code>{html.escape(name)}</code> · {cnt} 个下级节点" for name, cnt in active.items()]
    active_block = "\n".join(active_lines) if active_lines else "  _(无活跃管道)_"
    status_line = "🔔 <b>云端拓扑已更新并生效</b>" if changed else "✅ <b>云端拓扑无实质变动</b>"
    
    msg = f"""🔄 <b>云端拓扑同步报告</b>
━━━━━━━━━━━━━━━━━━━━━
{status_line}
⏱️ <b>链路耗时</b> · <code>{elapsed:.1f}</code> 秒
🕐 <b>核准时间</b> · <code>{datetime.now().strftime('%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━━
<b>[ 拓扑变更详情 ]</b>
{change_block}
━━━━━━━━━━━━━━━━━━━━━
<b>[ 活跃管道矩阵 ]</b>
{active_block}
━━━━━━━━━━━━━━━━━━━━━
🚀 引擎流转完毕，已完成静默热重载。
💡 发送 <code>{html.escape(cmd_prefix)}enable &lt;管道名&gt;</code> 唤醒新节点"""
    try:
        client.parse_mode = 'html'
        await client.send_message("me", msg, link_preview=False)
    except: pass

async def sync(client: TelegramClient, config: dict) -> tuple:
    folder_rules, old_cache = config.get("folder_rules", {}), config.get("_system_cache", {})
    report = {"discovered": [], "renamed": [], "deleted": [], "active": {}, "has_changes": False}
    result = await client(functions.messages.GetDialogFiltersRequest())
    tg_folders = [f for f in getattr(result, "filters", []) if isinstance(f, types.DialogFilter)]
    if not tg_folders: return folder_rules, old_cache, False, report

    config_changed = False
    current_tg_ids = []
    id_to_local = {v["id"]: name for name, v in folder_rules.items() if v.get("id") is not None}

    for folder in tg_folders:
        tg_id, tg_title = folder.id, get_folder_title(folder)
        current_tg_ids.append(tg_id)
        if tg_id in id_to_local:
            old_name = id_to_local[tg_id]
            if old_name != tg_title:
                entry = folder_rules.pop(old_name)
                entry["id"] = tg_id
                folder_rules[tg_title] = entry
                report["renamed"].append((old_name, tg_title))
                config_changed = True
        elif tg_title in folder_rules and folder_rules[tg_title].get("id") is None:
            folder_rules[tg_title]["id"] = tg_id
            config_changed = True
        elif tg_title not in folder_rules:
            folder_rules[tg_title] = {"id": tg_id, "enable": False, "alert_channel_id": None, "rules": {f"🟢 {tg_title}监控": "(示范词A|示范词B)"}}
            report["discovered"].append(tg_title)
            config_changed = True

    for name in list(folder_rules.keys()):
        fid = folder_rules[name].get("id")
        if fid is not None and fid not in current_tg_ids:
            del folder_rules[name]
            report["deleted"].append(name)
            config_changed = True

    new_cache = {}
    all_dialogs = await client.get_dialogs(limit=None)

    for folder in tg_folders:
        tg_title = get_folder_title(folder)
        folder_cfg = folder_rules.get(tg_title, {})
        if not folder_cfg.get("enable", False): continue
        target_ids, exclude_ids = set(), set()

        for peer in getattr(folder, "exclude_peers", []):
            pid = resolve_peer_id(peer)
            if pid != 0: exclude_ids.add(pid)
        for peer in getattr(folder, "include_peers", []):
            pid = resolve_peer_id(peer)
            if pid != 0: target_ids.add(pid)

        if getattr(folder, "groups", False) or getattr(folder, "broadcasts", False):
            for dialog in all_dialogs:
                if folder.groups and dialog.is_group: target_ids.add(dialog.id)
                elif folder.broadcasts and dialog.is_channel and not dialog.is_group: target_ids.add(dialog.id)

        target_ids = target_ids - exclude_ids
        new_cache[tg_title] = list(target_ids)
        report["active"][tg_title] = len(target_ids)

    cache_changed = configs_differ(old_cache, new_cache)
    report["has_changes"] = config_changed or cache_changed
    return folder_rules, new_cache, report["has_changes"], report

async def main():
    config = load_config()
    api_id, api_hash = config.get("api_id"), config.get("api_hash")
    notify_channel = config.get("notify_channel_id") or config.get("global_alert_channel_id")
    cmd_prefix = str(config.get("cmd_prefix") or "-")
    is_chatops = '--chatops' in sys.argv
    if not is_chatops: service_stop()
    try:
        async with TelegramClient(SESSION_NAME, int(api_id), api_hash) as client:
            t0 = datetime.now()
            folder_rules, new_cache, has_changes, report = await sync(client, config)
            if has_changes:
                config["folder_rules"]  = folder_rules
                config["_system_cache"] = new_cache
                save_config(config)
            elapsed = (datetime.now() - t0).total_seconds()
            await send_sync_report(client, notify_channel, report, elapsed, cmd_prefix)
    finally:
        if not is_chatops: service_start()

if __name__ == "__main__": asyncio.run(main())
