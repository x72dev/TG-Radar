import os, re, sys, json, asyncio, logging, subprocess, html
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from telethon import TelegramClient, events, functions, types, utils

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

# 🛡️ 核心屏蔽墙：彻底封印 Telethon 底层的刷屏日志 (如 Got difference for channel)
logging.getLogger('telethon').setLevel(logging.WARNING)

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

    def hot_reload(self, new_folder_rules, new_system_cache, new_auto_route):
        self.folder_rules = new_folder_rules
        self.system_cache = new_system_cache
        self.auto_route_rules = new_auto_route
        new_map, new_count = build_target_map(self.folder_rules, self.system_cache, self.global_alert)
        self.target_map.clear()
        self.target_map.update(new_map)
        self.valid_rules_count = new_count

def fmt_uptime(start: datetime) -> str:
    total = int((datetime.now() - start).total_seconds())
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    mins, _ = divmod(rest, 60)
    parts = []
    if days: parts.append(f"{days}天")
    if hours: parts.append(f"{hours}小时")
    if mins: parts.append(f"{mins}分")
    return " ".join(parts) or "不足1分钟"

def fmt_dt(dt: Optional[datetime]) -> str:
    return dt.strftime("%m-%d %H:%M:%S") if dt else "暂无记录"

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f: return json.load(f)
    except Exception as e:
        logger.error("配置文件解析发生致命异常: %s", e)
        sys.exit(1)

def _load_fresh_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f: return json.load(f)

def _save_config(cfg: dict) -> None:
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(cfg, f, indent=4, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)

def validate_config(config: dict) -> tuple:
    api_id, api_hash = config.get("api_id"), config.get("api_hash")
    if not api_id or not api_hash or api_id == 1234567:
        logger.error("引擎点火失败：未配置有效的 API 凭证。")
        sys.exit(1)
    global_alert = config.get("global_alert_channel_id")
    notify_channel = config.get("notify_channel_id") or global_alert
    cmd_prefix = str(config.get("cmd_prefix") or "-")
    auto_route = config.get("auto_route_rules", {})
    return int(api_id), str(api_hash), global_alert, notify_channel, cmd_prefix, auto_route

def build_target_map(folder_rules: dict, system_cache: dict, global_alert: Optional[int]) -> tuple:
    target_map, valid_rules_count = {}, 0
    for folder_name, ids_list in system_cache.items():
        folder_cfg = folder_rules.get(folder_name, {})
        if not folder_cfg.get("enable", False): continue
        alert_channel = folder_cfg.get("alert_channel_id") or global_alert
        if not alert_channel: continue
        compiled_rules = {}
        for level, pattern in folder_cfg.get("rules", {}).items():
            try:
                compiled_rules[level] = re.compile(pattern, re.IGNORECASE)
                valid_rules_count += 1
            except: pass
        if not compiled_rules: continue
        for chat_id in ids_list:
            target_map.setdefault(chat_id, []).append({"folder_name": folder_name, "alert_channel": int(alert_channel), "rules": compiled_rules})
    return target_map, valid_rules_count

def build_msg_link(chat, chat_id: int, msg_id: int) -> str:
    username = getattr(chat, "username", None)
    if username: return f"https://t.me/{username}/{msg_id}"
    raw = str(abs(chat_id))
    if raw.startswith("100") and len(raw) >= 12: return f"https://t.me/c/{raw[3:]}/{msg_id}"
    return ""

async def send_notify(client, notify_channel, text: str):
    target = notify_channel if notify_channel else "me"
    try: await client.send_message(target, text, link_preview=False)
    except: pass

async def send_startup_notification(client, notify_channel, state, cmd_prefix):
    lines = []
    for name, cfg in state.folder_rules.items():
        if cfg.get("enable", False):
            grp_cnt = len(state.system_cache.get(name, []))
            rule_cnt = len(cfg.get("rules", {}))
            lines.append(f"  ✅ <code>{html.escape(name)}</code> · {grp_cnt} 节点 · {rule_cnt} 策略")
    folder_block = "\n".join(lines) if lines else "  _(暂无活跃的监听拓扑)_"
    msg = f"""🚀 <b>TG-Radar 态势感知引擎已上线</b>
━━━━━━━━━━━━━━━━━━━━━
📡 <b>监控矩阵</b> · <code>{len(state.target_map)}</code> 节点
🛡️ <b>防护策略</b> · <code>{state.valid_rules_count}</code> 规则
🕐 <b>启动时间</b> · <code>{datetime.now().strftime('%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━━
<b>[ 活跃管道 ]</b>
{folder_block}
━━━━━━━━━━━━━━━━━━━━━
💡 向此发送 <code>{html.escape(cmd_prefix)}help</code> 呼出核心控制台"""
    
    last_msg_path = os.path.join(WORK_DIR, ".last_msg")
    if os.path.exists(last_msg_path):
        try:
            with open(last_msg_path, "r") as f: ctx = json.load(f)
            action = ctx.get("action", "restart")
            prefix_text = "✨ <b>[ OTA 固件更新完成 ]</b> 核心架构已热重载！\n\n" if action == "update" else ""
            await client.edit_message(ctx["chat_id"], ctx["msg_id"], prefix_text + msg)
            os.remove(last_msg_path)
            return
        except: pass
    await send_notify(client, notify_channel, msg)

def edit_config(modifier_fn) -> tuple:
    try:
        cfg = _load_fresh_config()
        modifier_fn(cfg)
        _save_config(cfg)
        return True
    except Exception as e: return False

def find_folder(folder_rules: dict, query: str) -> tuple:
    if query in folder_rules: return query, []
    for name in folder_rules:
        if name.lower() == query.lower(): return name, []
    candidates = [n for n in folder_rules if query.lower() in n.lower()]
    return (None, candidates) if candidates else (None, list(folder_rules.keys()))

async def apply_hot_reload(event, state: AppState, success_text: str):
    new_cfg = _load_fresh_config()
    state.hot_reload(new_cfg.get("folder_rules", {}), new_cfg.get("_system_cache", {}), new_cfg.get("auto_route_rules", {}))
    final_text = f"{success_text}\n━━━━━━━━━━━━━━━━━━━━━\n⚡ <b>策略已实时生效 (无感热重载)</b>"
    try: await event.edit(final_text)
    except: await event.reply(final_text)

async def auto_route_groups(client, auto_route_rules) -> bool:
    if not auto_route_rules: return False
    try:
        req = await client(functions.messages.GetDialogFiltersRequest())
        folders = [f for f in getattr(req, "filters", []) if isinstance(f, types.DialogFilter)]
        dialogs = await client.get_dialogs(limit=None)
        changes_made = False

        for folder_name, pattern_str in auto_route_rules.items():
            try: pattern = re.compile(pattern_str, re.IGNORECASE)
            except: continue

            target_folder = next((f for f in folders if (f.title.text if hasattr(f.title, 'text') else str(f.title)) == folder_name), None)
            if not target_folder: continue

            current_peer_ids = [utils.get_peer_id(p) for p in target_folder.include_peers]
            to_add = []

            for d in dialogs:
                if d.is_group and getattr(d, 'name', '') and pattern.search(d.name):
                    peer = utils.get_input_peer(d.entity)
                    peer_id = utils.get_peer_id(peer)
                    if peer_id not in current_peer_ids:
                        to_add.append(peer)

            if to_add:
                target_folder.include_peers.extend(to_add)
                await client(functions.messages.UpdateDialogFilterRequest(id=target_folder.id, filter=target_folder))
                changes_made = True
        return changes_made
    except Exception as e:
        logger.error("智能路由巡检异常: %s", e)
        return False

def register_handlers(client, state: AppState, notify_channel, cmd_prefix) -> None:
    p = cmd_prefix
    pe = html.escape(p)
    cmd_regex = re.compile(rf"^{re.escape(p)}(\w+)[ \t]*([\s\S]*)", re.IGNORECASE)

    async def _respond(event, text: str, auto_delete: int = 0):
        try: msg = await event.edit(text)
        except: msg = await event.reply(text)
        if msg and auto_delete > 0:
            async def schedule_delete():
                await asyncio.sleep(auto_delete)
                try: await msg.delete()
                except: pass
            asyncio.create_task(schedule_delete())

    # 🛡️ 终极权限锁定：仅监听 Saved Messages (收藏夹)
    @client.on(events.NewMessage(chats=["me"], pattern=cmd_regex))
    async def control_panel(event):
        command = event.pattern_match.group(1).lower()
        args = (event.pattern_match.group(2) or "").strip()
        try: await _dispatch(event, command, args)
        except Exception as exc:
            try: await _respond(event, f"❌ <b>内部异常</b>：<code>{html.escape(str(exc))}</code>")
            except: pass

    async def _dispatch(event, command: str, args: str):
        if command == "help":
            await _respond(event, f"""🤖 <b>TG-Radar 核心控制台</b>
<code>{p}ping</code> 心跳探测 | <code>{p}status</code> 监控大屏
<code>{p}log [n]</code> 系统日志 | <code>{p}folders</code> 活跃管道
<code>{p}rules &lt;分组&gt;</code> 策略明细
<code>{p}enable/disable &lt;分组&gt;</code> 唤醒/休眠管道
<code>{p}addrule &lt;分组&gt; &lt;规则名&gt; &lt;词&gt;</code> 挂载正则
<code>{p}delrule &lt;分组&gt; &lt;规则名&gt; [词]</code> 剥离正则
<code>{p}routes</code> 路由矩阵 | <code>{p}addroute/delroute</code> 配置路由
<code>{p}sync</code> 云端同步(热重载) | <code>{p}update</code> OTA更新""", auto_delete=60)
            
        elif command == "ping": await _respond(event, f"🟢 <b>SYS.PING</b> | UP: <code>{fmt_uptime(state.start_time)}</code> | 捕获量: <code>{state.total_hits}</code>", auto_delete=10)
        
        elif command == "status":
            last = f"<code>{html.escape(state.last_hit_folder)}</code> ({fmt_dt(state.last_hit_time)})" if state.last_hit_time else "暂无记录"
            enabled_cnt = sum(1 for cfg in state.folder_rules.values() if cfg.get("enable", False))
            await _respond(event, f"""⚡ <b>TG-Radar 监控大屏</b>
▸ 运行时长 : <code>{fmt_uptime(state.start_time)}</code>
▸ 拓扑矩阵 : <code>{len(state.target_map)}</code> 节点 · <code>{enabled_cnt}</code> 管道
▸ 智能路由 : <code>{len(state.auto_route_rules)}</code> 条策略
▸ 生效策略 : <code>{state.valid_rules_count}</code> 规则
▸ 累计拦截 : <code>{state.total_hits}</code> 次
▸ 最新捕获 : {last}""", auto_delete=20)
            
        elif command == "log":
            try: await event.edit("⏳ <b>获取日志中...</b>")
            except: pass
            n_lines = 20
            if args:
                try: n_lines = max(1, min(100, int(args)))
                except ValueError: return await _respond(event, f"❌ 行数参数无效：`{args}`")
            try:
                import html as _html
                import re as _re
                # 扩大抓取范围，以弥补被清洗掉的垃圾日志
                raw = subprocess.check_output(
                    ["journalctl", "-u", SERVICE_NAME, f"-n{n_lines*4}", "--no-pager", "--output=short-iso"],
                    text=True, stderr=subprocess.STDOUT
                )
                lines_out = []
                for line in raw.splitlines():
                    if line.startswith("--") or not line.strip(): continue
                    msg = line.split("]: ", 1)[-1] if "]: " in line else line
                    
                    # 🗑️ 垃圾日志强力清洗：剔除所有无价值的网络同步和底层状态
                    if any(x in msg for x in ["Got difference for channel", "Connecting to", "Connection to", "TcpFull"]):
                        continue
                        
                    try:
                        m = _re.match(r"^\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2}) \[(\w+)\] (.*)", msg.strip())
                        if m:
                            time_str, level, msg_content = m.groups()
                            icon = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌", "DEBUG": "🔍"}.get(level, "·")
                            lines_out.append(f"{icon} <code>{time_str}</code> {_html.escape(msg_content)}")
                        else:
                            if len(msg) < 200: lines_out.append(f"· {_html.escape(msg)}")
                    except: pass
                
                # 精准截断到用户请求的行数
                lines_out = lines_out[-n_lines:]
                if not lines_out: return await _respond(event, "📜 <b>日志</b> · 暂无可读的业务记录")
                
                log_body = "\n".join(lines_out)
                if len(log_body) > 3600: log_body = "…（已截断）\n" + log_body[-3500:]
                
                html_msg = f"📜 <b>系统核心日志</b> · 最新 {len(lines_out)} 条\n<blockquote expandable>{log_body}</blockquote>"
                try: await event.edit(html_msg, parse_mode='html')
                except: await event.reply(html_msg, parse_mode='html')
            except Exception as e:
                await _respond(event, f"❌ 获取日志失败: `{e}`")

        elif command == "folders":
            lines, enabled_cnt = [], 0
            for name, cfg in state.folder_rules.items():
                is_on = cfg.get("enable", False)
                rule_cnt, grp_cnt = len(cfg.get("rules", {})), len(state.system_cache.get(name, []))
                if is_on:
                    lines.append(f"✅ <b>{html.escape(name)}</b>\n   └ {grp_cnt} 节点 · {rule_cnt} 策略")
                    enabled_cnt += 1
                else: lines.append(f"⭕ {html.escape(name)}\n   └ {rule_cnt} 策略 · <i>(已休眠)</i>")
            body = "\n\n".join(lines) if lines else "<i>尚未建立拓扑</i>"
            await _respond(event, f"📂 <b>数据管道拓扑</b> | 活跃 <code>{enabled_cnt}/{len(state.folder_rules)}</code>\n\n{body}")

        elif command == "rules":
            if not args: return await _respond(event, f"❓ <b>语法</b>: <code>{pe}rules &lt;分组&gt;</code>")
            matched, _ = find_folder(state.folder_rules, args)
            if not matched: return await _respond(event, f"❌ 未找到管道 <code>{html.escape(args)}</code>")
            cfg = state.folder_rules[matched]
            rules_block = "\n\n".join([f"  {i}. <b>{html.escape(lvl)}</b>\n     <code>{html.escape(pat)}</code>" for i, (lvl, pat) in enumerate(cfg.get("rules", {}).items(), 1)]) if cfg.get("rules") else "  <i>(空)</i>"
            await _respond(event, f"📋 <b>{html.escape(matched)}</b> 策略明细\n\n{rules_block}")

        elif command in ["enable", "disable"]:
            matched, _ = find_folder(state.folder_rules, args)
            if not matched: return await _respond(event, "❌ 找不到该管道")
            tgt = (command == "enable")
            def do_toggle(cfg): cfg["folder_rules"][matched]["enable"] = tgt
            edit_config(do_toggle)
            await apply_hot_reload(event, state, f"{'✅' if tgt else '⭕'} <b>已{'唤醒' if tgt else '休眠'}数据管道</b> <code>{html.escape(matched)}</code>")

        elif command == "addrule":
            parts = args.split()
            matched, _ = find_folder(state.folder_rules, parts[0].strip())
            if not matched: return await _respond(event, "❌ 找不到该管道")
            rule_name = parts[1].strip()
            new_words = [re.escape(w.strip()) for w in parts[2:] if w.strip()]
            existing = state.folder_rules[matched].get("rules", {})
            current_words = set(t.strip() for t in existing.get(rule_name, "").strip("()").split("|") if t.strip())
            current_words.update(new_words)
            merged_pattern = "(" + "|".join(sorted(current_words)) + ")"
            def do_add(cfg): cfg["folder_rules"][matched].setdefault("rules", {})[rule_name] = merged_pattern
            edit_config(do_add)
            await apply_hot_reload(event, state, f"✅ <b>[ 监控策略已挂载 ]</b>\n▸ <b>策略</b> : <code>{html.escape(rule_name)}</code>")

        elif command == "delrule":
            parts = args.split()
            matched, _ = find_folder(state.folder_rules, parts[0].strip())
            if not matched: return await _respond(event, "❌ 找不到该管道")
            rule_name, remove_words = parts[1].strip(), set(re.escape(w.strip()) for w in parts[2:] if w.strip())
            existing = state.folder_rules[matched].get("rules", {})
            current_words = set(t.strip() for t in existing[rule_name].strip("()").split("|") if t.strip())
            remain_words = current_words - remove_words
            if not remove_words or not remain_words:
                def do_delall(cfg): del cfg["folder_rules"][matched]["rules"][rule_name]
                edit_config(do_delall)
                return await apply_hot_reload(event, state, f"🗑️ <b>[ 策略模块已废弃 ]</b>")
            new_pattern = "(" + "|".join(sorted(remain_words)) + ")"
            def do_update(cfg): cfg["folder_rules"][matched]["rules"][rule_name] = new_pattern
            edit_config(do_update)
            await apply_hot_reload(event, state, f"✂️ <b>[ 策略单元已精准剥离 ]</b>")

        elif command == "routes":
            lines = [f"  • <b>{html.escape(f)}</b> : <code>{html.escape(p)}</code>" for f, p in state.auto_route_rules.items()]
            block = "\n".join(lines) if lines else "  <i>(暂无智能路由策略)</i>"
            await _respond(event, f"🔀 <b>智能收纳路由表</b>\n\n{block}\n\n<i>发送 <code>{pe}addroute &lt;云端分组&gt; &lt;正则&gt;</code> 动态配置</i>")

        elif command == "addroute":
            parts = args.split(maxsplit=1)
            if len(parts) < 2: return await _respond(event, f"❓ <b>语法</b>: <code>{pe}addroute &lt;云端分组名&gt; &lt;匹配正则&gt;</code>")
            folder_name, regex = parts[0].strip(), parts[1].strip()
            try: re.compile(regex)
            except Exception as e: return await _respond(event, f"❌ <b>正则编译失败</b>: {e}")
            def do_addroute(cfg): cfg.setdefault("auto_route_rules", {})[folder_name] = regex
            edit_config(do_addroute)
            await apply_hot_reload(event, state, f"✅ <b>[ 智能路由已挂载 ]</b>\n▸ <b>目标分组</b> : <code>{html.escape(folder_name)}</code>\n▸ <b>匹配正则</b> : <code>{html.escape(regex)}</code>")

        elif command == "delroute":
            folder_name = args.strip()
            if folder_name not in state.auto_route_rules: return await _respond(event, "❌ 找不到该路由策略")
            def do_delroute(cfg): del cfg["auto_route_rules"][folder_name]
            edit_config(do_delroute)
            await apply_hot_reload(event, state, f"🗑️ <b>[ 智能路由已剔除 ]</b>\n▸ <b>解绑分组</b> : <code>{html.escape(folder_name)}</code>")

        elif command == "sync":
            try: await event.edit("🔄 <b>[ 拓扑云端全量同步 ]</b>\n> 正在执行热重载...")
            except: msg = await event.reply("🔄 <b>[ 拓扑云端全量同步 ]</b>\n> 正在执行热重载...")
            import sync_engine
            if 'sync_engine' in sys.modules: sys.modules.pop('sync_engine')
            import sync_engine
            cfg = _load_fresh_config()
            await auto_route_groups(client, cfg.get("auto_route_rules", {}))
            f_new, c_new, has_changes, report = await sync_engine.sync(client, cfg)
            if has_changes:
                cfg["folder_rules"], cfg["_system_cache"] = f_new, c_new
                _save_config(cfg)
                state.hot_reload(f_new, c_new, cfg.get("auto_route_rules", {}))
            await apply_hot_reload(event, state, "✅ <b>拓扑云端同步完成</b>")

        elif command == "update":
            await event.reply("🔄 <b>[ OTA 固件拉取更新 ]</b>\n> 正在从主分支同步原生代码...")
            with open(os.path.join(WORK_DIR, ".last_msg"), "w") as f:
                json.dump({"chat_id": event.chat_id, "msg_id": event.id, "action": "update"}, f)
            await asyncio.sleep(1)
            cmd = f"curl -fsSL https://github.com/chenmo8848/TG-Radar/archive/refs/heads/main.zip -o /tmp/tgr.zip && unzip -q -o /tmp/tgr.zip -d /tmp/ && cp -af /tmp/TG-Radar-main/. {WORK_DIR}/ && rm -rf /tmp/tgr.zip /tmp/TG-Radar-main"
            subprocess.run(cmd, shell=True)
            subprocess.Popen(["sudo", "systemctl", "restart", SERVICE_NAME])

        elif command == "restart":
            await event.reply("🔄 <b>[ 物理级系统重启 ]</b>\n正在通过 Systemd 重载守护进程...")
            with open(os.path.join(WORK_DIR, ".last_msg"), "w") as f:
                json.dump({"chat_id": event.chat_id, "msg_id": event.id, "action": "restart"}, f)
            await asyncio.sleep(1.5)
            subprocess.Popen(["sudo", "systemctl", "restart", SERVICE_NAME])

    @client.on(events.NewMessage)
    async def message_handler(event):
        try:
            if not (event.is_group or event.is_channel) or event.chat_id not in state.target_map: return
            msg_text = event.raw_text
            if not msg_text: return
            chat, chat_title, sender_name, sender_loaded = None, "", "", False
            for task in state.target_map[event.chat_id]:
                for level, pattern in task["rules"].items():
                    match = pattern.search(msg_text)
                    if not match: continue
                    if not sender_loaded:
                        sender_loaded = True
                        chat = await event.get_chat()
                        chat_title = getattr(chat, "title", "未知链路")
                        try:
                            sender = await event.get_sender()
                            if getattr(sender, "bot", False): return
                            sender_name = getattr(sender, "username", "") or getattr(sender, "first_name", "") or "隐藏域载体"
                        except: sender_name = "公海信道"
                    
                    preview = html.escape(msg_text[:1000])
                    msg_link = build_msg_link(chat, event.chat_id, event.id)
                    alert_text = f"🚨 <b>[ 情报雷达告警 ]</b>\n🎯 <b>词汇</b> : <code>{html.escape(match.group(0))}</code>\n🏷️ <b>策略</b> : <code>{html.escape(level)}</code> ({html.escape(task['folder_name'])})\n📡 <b>来源</b> : <code>{html.escape(chat_title)}</code>\n👤 <b>载体</b> : @{html.escape(sender_name)}\n<b>[ 现场原始快照 ]</b>\n<blockquote expandable>{preview}</blockquote>"
                    if msg_link: alert_text += f'\n🔗 <a href="{msg_link}">直达情报源</a>'
                    try:
                        await client.send_message(task["alert_channel"], alert_text, link_preview=False)
                        state.total_hits += 1
                        state.last_hit_folder = task["folder_name"]
                        state.last_hit_time = datetime.now()
                    except: pass
                    break
        except Exception as e:
            logger.error("消息解析流转异常: %s", e)

async def main():
    config = load_config()
    api_id, api_hash, global_alert, notify_channel, cmd_prefix, auto_route = validate_config(config)
    
    state = AppState()
    state.global_alert = global_alert
    state.hot_reload(config.get("folder_rules", {}), config.get("_system_cache", {}), auto_route)

    async with TelegramClient(SESSION_NAME, api_id, api_hash) as client:
        client.parse_mode = 'html'
        
        # 🛡️ 内部静默巡检任务：接管加群路由与拓扑刷新
        async def internal_auto_sync():
            while True:
                await asyncio.sleep(1800) # 每 30 分钟循环一次
                try:
                    cfg = _load_fresh_config()
                    await auto_route_groups(client, cfg.get("auto_route_rules", {}))
                    import sync_engine
                    if 'sync_engine' in sys.modules: sys.modules.pop('sync_engine')
                    import sync_engine
                    f_new, c_new, changed, _ = await sync_engine.sync(client, cfg)
                    if changed:
                        cfg["folder_rules"], cfg["_system_cache"] = f_new, c_new
                        _save_config(cfg)
                        state.hot_reload(f_new, c_new, cfg.get("auto_route_rules", {}))
                        logger.info("📡 内部巡检：已发现新拓扑并完成热重载。")
                except Exception as e:
                    logger.error("内部巡检异常: %s", e)

        asyncio.create_task(internal_auto_sync())
        
        register_handlers(client, state, notify_channel, cmd_prefix)
        await send_startup_notification(client, notify_channel, state, cmd_prefix)
        await client.run_until_disconnected()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
