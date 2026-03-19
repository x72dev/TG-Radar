import os, re, sys, json, asyncio, logging, subprocess, html
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from telethon import TelegramClient, events, functions, types, utils

# 🛡️ 屏蔽底层噪声日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logging.getLogger('telethon').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(WORK_DIR, "config.json")
SESSION_NAME = os.path.join(WORK_DIR, "TG_Radar_session")
SERVICE_NAME = "tg_monitor"

@dataclass
class AppState:
    start_time: datetime = field(default_factory=datetime.now)
    total_hits: int = 0
    last_hit_folder: str = ""
    last_hit_time: Optional[datetime] = None
    target_map: dict = field(default_factory=dict)
    valid_rules_count: int = 0
    folder_rules: dict = field(default_factory=dict)
    system_cache: dict = field(default_factory=dict)
    auto_route_rules: dict = field(default_factory=dict)
    global_alert: Optional[int] = None

    def hot_reload(self, cfg):
        self.folder_rules = cfg.get("folder_rules", {})
        self.system_cache = cfg.get("_system_cache", {})
        self.auto_route_rules = cfg.get("auto_route_rules", {})
        self.global_alert = cfg.get("global_alert_channel_id")
        self.target_map, self.valid_rules_count = build_target_map(self.folder_rules, self.system_cache, self.global_alert)

async def schedule_delete(msg, delay: int):
    if not msg or delay <= 0: return
    await asyncio.sleep(delay)
    try: await msg.delete()
    except: pass

def fmt_uptime(start: datetime) -> str:
    total = int((datetime.now() - start).total_seconds())
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    mins, _ = divmod(rest, 60)
    return f"{days}天 {hours}h {mins}m" if days else f"{hours}h {mins}m"

def _save_config(cfg: dict) -> None:
    # 💡 自动注入直观的中文功能描述
    pretty_cfg = {
        "_说明_1": "👇【核心凭证】前往 my.telegram.org 获取",
        "api_id": cfg.get("api_id"), "api_hash": cfg.get("api_hash"),
        "_说明_2": "👇【告警路由】global_alert为默认频道，notify为通知频道",
        "global_alert_channel_id": cfg.get("global_alert_channel_id"),
        "notify_channel_id": cfg.get("notify_channel_id"),
        "_说明_3": "👇【交互前缀】你在收藏夹指令的前缀，如 -",
        "cmd_prefix": cfg.get("cmd_prefix", "-"),
        "_说明_4": "👇【智能路由】{\"分组名\": \"群名正则\"} 系统自动拉群",
        "auto_route_rules": cfg.get("auto_route_rules", {}),
        "_说明_5": "👇【系统缓存】禁止手动修改以下内容",
        "folder_rules": cfg.get("folder_rules", {}), "_system_cache": cfg.get("_system_cache", {})
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(pretty_cfg, f, indent=4, ensure_ascii=False)

def build_target_map(folder_rules, system_cache, global_alert):
    t_map, count = {}, 0
    for f_name, ids in system_cache.items():
        f_cfg = folder_rules.get(f_name, {})
        if not f_cfg.get("enable"): continue
        alert_ch = f_cfg.get("alert_channel_id") or global_alert
        if not alert_ch: continue
        compiled = {lvl: re.compile(pat, re.IGNORECASE) for lvl, pat in f_cfg.get("rules", {}).items()}
        count += len(compiled)
        for cid in ids: t_map.setdefault(cid, []).append({"f_name": f_name, "alert_ch": int(alert_ch), "rules": compiled})
    return t_map, count

async def auto_route_groups(client, rules) -> bool:
    if not rules: return False
    try:
        res = await client(functions.messages.GetDialogFiltersRequest())
        fds = [f for f in getattr(res, 'filters', []) if isinstance(f, types.DialogFilter)]
        dialogs = await client.get_dialogs(limit=None)
        changed = False
        for f_name, pattern_str in rules.items():
            pattern = re.compile(pattern_str, re.IGNORECASE)
            target_f = next((f for f in fds if (f.title.text if hasattr(f.title,'text') else str(f.title)) == f_name), None)
            if not target_f: continue
            curr_ids = [utils.get_peer_id(p) for p in target_f.include_peers]
            to_add = [utils.get_input_peer(d.entity) for d in dialogs if d.is_group and pattern.search(d.name) and utils.get_peer_id(d.entity) not in curr_ids]
            if to_add:
                target_f.include_peers.extend(to_add)
                await client(functions.messages.UpdateDialogFilterRequest(id=target_f.id, filter=target_f))
                changed = True
        return changed
    except: return False

def register_handlers(client, state: AppState, p):
    pe = html.escape(p)
    cmd_regex = re.compile(rf"^{re.escape(p)}(\w+)[ \t]*([\s\S]*)", re.IGNORECASE)

    async def _respond(event, text, delay=20):
        try: m = await event.edit(text)
        except: m = await event.reply(text)
        if m and delay > 0: asyncio.create_task(schedule_delete(m, delay))

    @client.on(events.NewMessage(chats=["me"], pattern=cmd_regex))
    async def control_panel(event):
        cmd = event.pattern_match.group(1).lower()
        args = (event.pattern_match.group(2) or "").strip()
        
        if cmd == "help":
            await _respond(event, f"🤖 <b>TG-Radar 控制台</b>\n\n<b>▸ 观测</b>: <code>{pe}ping</code> | <code>{pe}status</code> | <code>{pe}log 20</code>\n<b>▸ 管道</b>: <code>{pe}folders</code> | <code>{pe}enable 分组</code>\n<b>▸ 策略</b>: <code>{pe}addrule 分组 规则 正则</code>\n<b>▸ 路由</b>: <code>{pe}routes</code> | <code>{pe}addroute 分组 正则</code>\n<b>▸ 维护</b>: <code>{pe}sync</code> | <code>{pe}update</code>", 45)

        elif cmd == "status":
            enabled = sum(1 for c in state.folder_rules.values() if c.get("enable"))
            await _respond(event, f"⚡ <b>监控大屏</b>\n▸ 运行: <code>{fmt_uptime(state.start_time)}</code>\n▸ 节点: <code>{len(state.target_map)}</code> | 策略: <code>{state.valid_rules_count}</code>\n▸ 拦截: <code>{state.total_hits}</code> | 活跃管道: <code>{enabled}</code>", 30)

        elif cmd == "log":
            n = int(args) if args.isdigit() else 20
            try:
                raw = subprocess.check_output(["journalctl", "-u", SERVICE_NAME, f"-n{n*4}", "--no-pager"], text=True)
                lines = [f"· {html.escape(l[-100:])}" for l in raw.splitlines() if not any(x in l for x in ["Got diff", "Connect"])]
                await _respond(event, f"📜 <b>日志预览</b>\n<blockquote expandable>{'<b>\n</b>'.join(lines[-n:])}</blockquote>", 60)
            except: await _respond(event, "❌ 获取失败", 10)

        elif cmd == "folders":
            lines = [f"{'✅' if c.get('enable') else '⭕'} <b>{n}</b> ({len(c.get('rules',{}))}规)" for n,c in state.folder_rules.items()]
            await _respond(event, "📂 <b>管道拓扑</b>\n\n" + "\n".join(lines), 45)

        elif cmd in ["enable", "disable"]:
            m, _ = find_folder(state.folder_rules, args)
            if not m: return await _respond(event, "❌ 找不到该管道", 10)
            on = (cmd == "enable")
            cfg = _load_fresh_config()
            cfg["folder_rules"][m]["enable"] = on
            _save_config(cfg)
            state.hot_reload(cfg)
            await _respond(event, f"{'✅' if on else '⭕'} <b>{m}</b> 已{'唤醒' if on else '休眠'}", 10)

        elif cmd == "addrule":
            ps = args.split(maxsplit=2)
            if len(ps)<3: return await _respond(event, f"❓ {pe}addrule 分组 规则 正则", 10)
            m, _ = find_folder(state.folder_rules, ps[0])
            if not m: return await _respond(event, "❌ 找不到分组", 10)
            cfg = _load_fresh_config()
            cfg["folder_rules"][m].setdefault("rules", {})[ps[1]] = f"({ps[2]})"
            _save_config(cfg)
            state.hot_reload(cfg)
            await _respond(event, f"✅ 策略 <b>{ps[1]}</b> 已挂载至 <b>{m}</b>", 15)

        elif cmd == "routes":
            rs = [f"• <b>{n}</b> : <code>{r}</code>" for n,r in state.auto_route_rules.items()]
            await _respond(event, "🔀 <b>智能路由表</b>\n\n" + ("\n".join(rs) if rs else "空"), 30)

        elif cmd == "addroute":
            ps = args.split(maxsplit=1)
            if len(ps)<2: return await _respond(event, f"❓ {pe}addroute 分组 正则", 10)
            cfg = _load_fresh_config()
            cfg.setdefault("auto_route_rules", {})[ps[0]] = ps[1]
            _save_config(cfg)
            state.hot_reload(cfg)
            await _respond(event, f"✅ 路由 <b>{ps[0]}</b> 已指向 <code>{ps[1]}</code>", 15)

        elif cmd == "update":
            m = await event.reply("🔄 <b>OTA 固件同步中...</b>")
            with open(os.path.join(WORK_DIR, ".last_msg"), "w") as f:
                json.dump({"chat_id": event.chat_id, "msg_id": m.id, "action": "update"}, f)
            up_cmd = f"curl -L https://github.com/chenmo8848/TG-Radar/archive/refs/heads/main.zip -o /tmp/tgr.zip && unzip -q -o /tmp/tgr.zip -d /tmp/ && cp -af /tmp/TG-Radar-main/. {WORK_DIR}/ && sudo systemctl restart {SERVICE_NAME}"
            subprocess.Popen(["/bin/bash", "-c", up_cmd])

        elif cmd == "restart":
            m = await event.reply("🔄 <b>正在执行物理重启...</b>")
            with open(os.path.join(WORK_DIR, ".last_msg"), "w") as f:
                json.dump({"chat_id": event.chat_id, "msg_id": m.id, "action": "restart"}, f)
            subprocess.Popen(["sudo", "systemctl", "restart", SERVICE_NAME])

        elif cmd == "sync":
            m = await event.edit("🔄 正在同步云端拓扑...")
            import sync_engine
            if 'sync_engine' in sys.modules: sys.modules.pop('sync_engine')
            import sync_engine
            cfg = _load_fresh_config()
            await auto_route_groups(client, cfg.get("auto_route_rules", {}))
            f_n, c_n, chg, _ = await sync_engine.sync(client, cfg)
            if chg:
                cfg["folder_rules"], cfg["_system_cache"] = f_n, c_n
                _save_config(cfg)
                state.hot_reload(cfg)
            await _respond(event, "✅ 拓扑全量同步完成", 15)

    @client.on(events.NewMessage)
    async def message_handler(event):
        if not (event.is_group or event.is_channel) or event.chat_id not in state.target_map: return
        txt = event.raw_text
        if not txt: return
        for task in state.target_map[event.chat_id]:
            for lvl, pat in task["rules"].items():
                m = pat.search(txt)
                if not m: continue
                chat = await event.get_chat()
                link = build_msg_link(chat, event.chat_id, event.id)
                alert = f"🚨 <b>[ 情报告警 ]</b>\n🎯 命中: <code>{html.escape(m.group(0))}</code>\n🏷️ 策略: {lvl} ({task['f_name']})\n📡 来源: {getattr(chat, 'title', '未知')}\n<blockquote expandable>{html.escape(txt[:500])}</blockquote>"
                if link: alert += f'\n🔗 <a href="{link}">直达源</a>'
                await client.send_message(task["alert_ch"], alert, link_preview=False)
                state.total_hits += 1
                state.last_hit_folder, state.last_hit_time = task["f_name"], datetime.now()
                return

def find_folder(folder_rules, query):
    if query in folder_rules: return query, []
    cand = [n for n in folder_rules if query.lower() in n.lower()]
    return (cand[0], []) if len(cand)==1 else (None, cand or list(folder_rules.keys()))

def _load_fresh_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f: return json.load(f)

async def main():
    cfg = load_config()
    _save_config(cfg) # 初始化注入中文说明
    state = AppState()
    state.hot_reload(cfg)
    async with TelegramClient(SESSION_NAME, int(cfg["api_id"]), cfg["api_hash"]) as client:
        client.parse_mode = 'html'
        register_handlers(client, state, str(cfg.get("cmd_prefix") or "-"))
        
        # 上线通知逻辑
        target = cfg.get("notify_channel_id") or "me"
        m_obj = None
        last_path = os.path.join(WORK_DIR, ".last_msg")
        if os.path.exists(last_path):
            try:
                with open(last_path, "r") as f: ctx = json.load(f)
                prefix = "✨ <b>OTA 固件更新完成</b>\n\n" if ctx.get("action")=="update" else "🔄 <b>系统重启完成</b>\n\n"
                m_obj = await client.edit_message(ctx["chat_id"], ctx["msg_id"], prefix + f"🚀 <b>TG-Radar 引擎已就绪</b>\n监控节点: <code>{len(state.target_map)}</code>")
                os.remove(last_path)
            except: pass
        if not m_obj: m_obj = await client.send_message(target, f"🚀 <b>TG-Radar 引擎已就绪</b>\n监控节点: <code>{len(state.target_map)}</code>")
        if m_obj: asyncio.create_task(schedule_delete(m_obj, 60))

        # 内部静默巡检
        async def patrol():
            while True:
                await asyncio.sleep(1800)
                try:
                    c = _load_fresh_config()
                    if await auto_route_groups(client, c.get("auto_route_rules")):
                        _save_config(c); state.hot_reload(c)
                except: pass
        asyncio.create_task(patrol())
        await client.run_until_disconnected()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
