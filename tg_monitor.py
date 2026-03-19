import os, re, sys, json, asyncio, logging, subprocess, html, importlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from telethon import TelegramClient, events, functions, types, utils

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)
logging.getLogger('telethon').setLevel(logging.WARNING)

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(WORK_DIR, "config.json")
SESSION_NAME = os.path.join(WORK_DIR, "TG_Radar_session")
SERVICE_NAME = "tg_monitor"
MONITOR_LOG_PATH = os.path.join(WORK_DIR, "monitor.log")

ROUTE_QUEUE = asyncio.Queue()

def write_biz_log(action: str, detail: str):
    time_str = datetime.now().strftime("%y-%m-%d %H:%M:%S")
    icon = {"HIT": "🎯", "SYNC": "🔄", "SYS": "⚙️", "ERR": "❌"}.get(action, "·")
    log_line = f"{icon} [{time_str}] {detail}\n"
    try:
        with open(MONITOR_LOG_PATH, "a", encoding="utf-8") as f: f.write(log_line)
    except Exception as e: logger.error(f"写入日志失败: {e}")

async def schedule_delete(msg, delay: int):
    if not msg or delay <= 0: return
    await asyncio.sleep(delay)
    try: await msg.delete()
    except: pass

async def safe_reply(event, text: str, auto_delete: int = 15):
    msg = None
    try: msg = await event.edit(text)
    except:
        try: msg = await event.reply(text)
        except: return None
    if msg and auto_delete > 0:
        asyncio.create_task(schedule_delete(msg, auto_delete))
    return msg

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
    desc_cfg = {
        "_说明_1": "👇【核心通信凭证】前往 my.telegram.org 获取，切勿泄露",
        "api_id": cfg.get("api_id", 1234567),
        "api_hash": cfg.get("api_hash", "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
        "_说明_2": "👇【消息流转设置】alert为默认告警频道，notify为系统通知频道(留null则发给收藏夹)",
        "global_alert_channel_id": cfg.get("global_alert_channel_id"),
        "notify_channel_id": cfg.get("notify_channel_id"),
        "_说明_3": "👇【交互控制台】你在TG收藏夹里触发命令的前缀符号，默认是减号 -",
        "cmd_prefix": cfg.get("cmd_prefix", "-"),
        "_说明_4": "👇【智能收纳路由】只要加入的新群名符合正则，系统会自动将其拉入指定的TG分组",
        "auto_route_rules": cfg.get("auto_route_rules", {}),
        "_说明_5": "👇【系统生成区】雷达的规则和群组拓扑缓存，请通过机器人指令修改，勿手动编辑",
        "folder_rules": cfg.get("folder_rules", {}),
        "_system_cache": cfg.get("_system_cache", {})
    }
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(desc_cfg, f, indent=4, ensure_ascii=False)
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

async def send_startup_notification(client, notify_channel, state, cmd_prefix):
    lines = []
    enabled_cnt = 0
    for name, cfg in state.folder_rules.items():
        if cfg.get("enable", False):
            grp_cnt = len(state.system_cache.get(name, []))
            rule_cnt = len(cfg.get("rules", {}))
            lines.append(f"  ✅ <code>{html.escape(name)}</code> · {grp_cnt} 节点 · {rule_cnt} 策略")
            enabled_cnt += 1
    folder_block = "\n".join(lines) if lines else "  <i>(暂无活跃的监听拓扑)</i>"
    
    route_lines = []
    for f_name, pat in state.auto_route_rules.items():
        route_lines.append(f"  🔀 <code>{html.escape(f_name)}</code> : <code>{html.escape(pat)}</code>")
    route_block = "\n".join(route_lines) if route_lines else "  <i>(暂无智能路由策略)</i>"
    
    msg = f"""🚀 <b>TG-Radar 态势感知引擎已上线</b>

<b>[ 引擎状态 ]</b>
▸ 监控矩阵 : <code>{len(state.target_map)}</code> 节点 · <code>{enabled_cnt}</code> 管道
▸ 智能路由 : <code>{len(state.auto_route_rules)}</code> 策略
▸ 防护策略 : <code>{state.valid_rules_count}</code> 规则
▸ 启动时间 : <code>{datetime.now().strftime('%m-%d %H:%M:%S')}</code>

<b>[ 活跃管道 ]</b>
{folder_block}

<b>[ 智能路由 ]</b>
{route_block}

💡 <i>提示: 发送 {html.escape(cmd_prefix)}help 唤出极客控制台。</i>"""
    
    last_msg_path = os.path.join(WORK_DIR, ".last_msg")
    target = notify_channel if notify_channel else "me"
    msg_obj = None

    if os.path.exists(last_msg_path):
        try:
            with open(last_msg_path, "r") as f: ctx = json.load(f)
            action = ctx.get("action", "restart")
            prefix_text = "✨ <b>[ OTA 固件更新完成 ]</b> 核心架构已热重载！\n\n" if action == "update" else "🔄 <b>[ 守护进程重启完成 ]</b>\n\n"
            msg_obj = await client.edit_message("me", ctx["msg_id"], prefix_text + msg)
            os.remove(last_msg_path)
            write_biz_log("SYS", f"系统恢复上线 (原因: {action})")
        except: pass
        
    if not msg_obj:
        try: msg_obj = await client.send_message(target, msg, link_preview=False)
        except: pass
        
    if msg_obj: asyncio.create_task(schedule_delete(msg_obj, 60))

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

async def apply_hot_reload(event, state: AppState, success_text: str, auto_delete: int = 15):
    new_cfg = _load_fresh_config()
    state.hot_reload(new_cfg.get("folder_rules", {}), new_cfg.get("_system_cache", {}), new_cfg.get("auto_route_rules", {}))
    final_text = f"{success_text}\n⚡ <b>策略已实时生效</b>"
    await safe_reply(event, final_text, auto_delete)

async def route_task_worker(client):
    while True:
        task = await ROUTE_QUEUE.get()
        try:
            await client(functions.messages.UpdateDialogFilterRequest(
                id=task['folder_id'],
                filter=task['folder_obj']
            ))
            write_biz_log("SYS", f"后台任务：成功向 TG 分组 [{task['name']}] 补充了 {task['cnt']} 个会话")
        except Exception as e:
            write_biz_log("ERR", f"后台任务：向分组 [{task['name']}] 同步数据失败: {e}")
        finally:
            ROUTE_QUEUE.task_done()
        await asyncio.sleep(4)

async def auto_route_groups(client, auto_route_rules) -> dict:
    # 🔥 修复了 errors 键丢失导致 KeyError 的 bug
    report = {"queued": {}, "missing": [], "matched_zero": [], "already_in": {}, "created": [], "errors": {}}
    if not auto_route_rules: return report
    
    try:
        req = await client(functions.messages.GetDialogFiltersRequest())
        folders = [f for f in getattr(req, "filters", []) if isinstance(f, types.DialogFilter)]
        
        all_dialogs = []
        async for d in client.iter_dialogs():
            if not (d.is_group or d.is_channel): continue
            name = utils.get_display_name(d.entity) or getattr(d, 'name', '') or getattr(d, 'title', '') or ''
            all_dialogs.append({'peer': utils.get_input_peer(d.entity), 'id': d.id, 'name': name})

        for folder_name, pattern_str in auto_route_rules.items():
            try: pattern = re.compile(pattern_str, re.IGNORECASE)
            except: continue

            target_folder = next((f for f in folders if (f.title.text if hasattr(f.title, 'text') else str(f.title)) == folder_name), None)
            
            matched_peers_info = []
            for d in all_dialogs:
                if pattern.search(d['name']):
                    matched_peers_info.append(d)

            if not matched_peers_info:
                report["matched_zero"].append(folder_name)
                continue

            if not target_folder:
                used_ids = [f.id for f in folders]
                new_id = 2
                while new_id in used_ids: new_id += 1
                
                peers_to_add = [m['peer'] for m in matched_peers_info]
                
                target_folder = types.DialogFilter(
                    id=new_id,
                    title=folder_name,
                    pinned_peers=[],
                    include_peers=peers_to_add,
                    exclude_peers=[],
                    contacts=False, non_contacts=False, groups=False,
                    broadcasts=False, bots=False, exclude_muted=False,
                    exclude_read=False, exclude_archived=False
                )
                
                folders.append(target_folder)
                ROUTE_QUEUE.put_nowait({
                    'folder_id': new_id,
                    'folder_obj': target_folder,
                    'name': folder_name,
                    'cnt': len(peers_to_add)
                })
                
                report["created"].append(folder_name)
                report["queued"][folder_name] = len(peers_to_add)
                continue

            current_peer_ids = []
            if hasattr(target_folder, "include_peers"):
                for p in target_folder.include_peers:
                    try: current_peer_ids.append(utils.get_peer_id(p))
                    except: pass
            else:
                target_folder.include_peers = []

            to_add = []
            already_cnt = 0

            for m in matched_peers_info:
                if m['id'] not in current_peer_ids:
                    to_add.append(m['peer'])
                    current_peer_ids.append(m['id'])
                else:
                    already_cnt += 1

            if not to_add:
                report["already_in"][folder_name] = already_cnt
                continue

            target_folder.include_peers.extend(to_add)
            ROUTE_QUEUE.put_nowait({
                'folder_id': target_folder.id,
                'folder_obj': target_folder,
                'name': folder_name,
                'cnt': len(to_add)
            })
            report["queued"][folder_name] = len(to_add)

    except Exception as e:
        logger.error(f"智能路由引擎扫描崩溃: {e}")
        
    return report

def register_handlers(client, state: AppState, notify_channel, cmd_prefix) -> None:
    p = cmd_prefix
    pe = html.escape(p)
    cmd_regex = re.compile(rf"^{re.escape(p)}(\w+)[ \t]*([\s\S]*)", re.IGNORECASE)

    @client.on(events.NewMessage(chats=["me"], pattern=cmd_regex))
    async def control_panel(event):
        command = event.pattern_match.group(1).lower()
        args = (event.pattern_match.group(2) or "").strip()
        try: await _dispatch(event, command, args)
        except Exception as exc:
            try: await safe_reply(event, f"❌ <b>内部异常</b>：<code>{html.escape(str(exc))}</code>", 15)
            except: pass

    async def _dispatch(event, command: str, args: str):
        if command == "help":
            await safe_reply(event, f"""🤖 <b>TG-Radar 极客控制台</b>

<b>[ 态势观测 ]</b>
<code>{p}ping</code> 引擎心跳
<code>{p}status</code> 监控大屏
<code>{p}log [行数]</code> 查阅纯净业务日志

<b>[ 策略调度 ]</b>
<code>{p}folders</code> 活跃管道
<code>{p}rules 分组名</code> 策略明细
<code>{p}enable 分组名</code> 唤醒管道
<code>{p}disable 分组名</code> 休眠管道
<code>{p}addrule 分组名 规则名 正则</code> 挂载规则
<code>{p}delrule 分组名 规则名 正则</code> 剔除规则

<b>[ 智能路由 ]</b>
<code>{p}routes</code> 路由矩阵
<code>{p}addroute 分组名 正则</code> 配置路由
<code>{p}delroute 分组名</code> 删除路由

<b>[ 系统指令 ]</b>
<code>{p}sync</code> 云端同步
<code>{p}update</code> OTA更新
<code>{p}restart</code> 重启引擎

💡 <i>注：所有面板均在 45s 内自动无痕销毁。</i>""", auto_delete=45)
            
        elif command == "ping": 
            await safe_reply(event, f"🟢 <b>SYS.PING</b> | UP: <code>{fmt_uptime(state.start_time)}</code> | 捕获量: <code>{state.total_hits}</code>", auto_delete=10)
        
        elif command == "status":
            last = f"<code>{html.escape(state.last_hit_folder)}</code> ({fmt_dt(state.last_hit_time)})" if state.last_hit_time else "暂无记录"
            enabled_cnt = sum(1 for cfg in state.folder_rules.values() if cfg.get("enable", False))
            
            queue_size = ROUTE_QUEUE.qsize()
            q_info = f" · ⏳ {queue_size} 个同步任务排队中" if queue_size > 0 else ""
            
            await safe_reply(event, f"""⚡ <b>TG-Radar 监控大屏</b>
▸ 运行时长 : <code>{fmt_uptime(state.start_time)}</code>
▸ 拓扑矩阵 : <code>{len(state.target_map)}</code> 节点 · <code>{enabled_cnt}</code> 管道
▸ 智能路由 : <code>{len(state.auto_route_rules)}</code> 条策略{q_info}
▸ 生效策略 : <code>{state.valid_rules_count}</code> 规则
▸ 累计拦截 : <code>{state.total_hits}</code> 次
▸ 最新捕获 : {last}""", auto_delete=30)
            
        elif command == "log":
            try: await event.edit("⏳ <b>读取持久化日志中...</b>")
            except: pass
            n_lines = 20
            if args and args.isdigit(): n_lines = max(1, min(100, int(args)))
            try:
                if not os.path.exists(MONITOR_LOG_PATH):
                    return await safe_reply(event, "📜 <b>纯净业务日志</b> · 暂无本地记录", 15)
                with open(MONITOR_LOG_PATH, "r", encoding="utf-8") as f: lines = f.readlines()
                lines_out = lines[-n_lines:]
                if not lines_out: return await safe_reply(event, "📜 <b>纯净业务日志</b> · 暂无记录", 15)
                log_body = "".join(lines_out).strip()
                if len(log_body) > 3600: log_body = "…（已截断）\n" + log_body[-3500:]
                html_msg = f"📜 <b>系统核心日志</b> · 最近 {len(lines_out)} 条\n<blockquote expandable>{html.escape(log_body)}</blockquote>"
                await safe_reply(event, html_msg, auto_delete=60)
            except Exception as e:
                await safe_reply(event, f"❌ 读取日志失败: `{e}`", 15)

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
            await safe_reply(event, f"📂 <b>数据管道拓扑</b> | 活跃 <code>{enabled_cnt}/{len(state.folder_rules)}</code>\n\n{body}", auto_delete=45)

        elif command == "rules":
            if not args: return await safe_reply(event, f"❓ 语法: {pe}rules 分组名", 15)
            matched, _ = find_folder(state.folder_rules, args)
            if not matched: return await safe_reply(event, f"❌ 未找到管道: <code>{html.escape(args)}</code>", 15)
            cfg = state.folder_rules[matched]
            rules_block = "\n\n".join([f"  {i}. <b>{html.escape(lvl)}</b>\n     <code>{html.escape(pat)}</code>" for i, (lvl, pat) in enumerate(cfg.get("rules", {}).items(), 1)]) if cfg.get("rules") else "  <i>(空)</i>"
            await safe_reply(event, f"📋 <b>{html.escape(matched)}</b> 策略明细\n\n{rules_block}", auto_delete=45)

        elif command in ["enable", "disable"]:
            if not args: return await safe_reply(event, f"❓ 语法: {pe}{command} 分组名", 15)
            matched, _ = find_folder(state.folder_rules, args)
            if not matched: return await safe_reply(event, "❌ 找不到该管道", 15)
            tgt = (command == "enable")
            def do_toggle(cfg): cfg["folder_rules"][matched]["enable"] = tgt
            edit_config(do_toggle)
            write_biz_log("SYS", f"{'唤醒' if tgt else '休眠'}管道: {matched}")
            await apply_hot_reload(event, state, f"{'✅' if tgt else '⭕'} <b>已{'唤醒' if tgt else '休眠'}数据管道</b> <code>{html.escape(matched)}</code>", 15)

        elif command == "addrule":
            parts = args.split(maxsplit=2)
            if len(parts) < 3: return await safe_reply(event, f"❓ 语法: {pe}addrule 分组名 规则名 匹配正则", 15)
            matched, _ = find_folder(state.folder_rules, parts[0].strip())
            if not matched: return await safe_reply(event, "❌ 找不到该管道", 15)
            rule_name = parts[1].strip()
            new_words = [re.escape(w.strip()) for w in parts[2].split() if w.strip()]
            existing = state.folder_rules[matched].get("rules", {})
            current_words = set(t.strip() for t in existing.get(rule_name, "").strip("()").split("|") if t.strip())
            current_words.update(new_words)
            merged_pattern = "(" + "|".join(sorted(current_words)) + ")"
            def do_add(cfg): cfg["folder_rules"][matched].setdefault("rules", {})[rule_name] = merged_pattern
            edit_config(do_add)
            write_biz_log("SYS", f"挂载策略: {rule_name} 到 {matched}")
            await apply_hot_reload(event, state, f"✅ <b>[ 监控策略已挂载 ]</b>\n▸ <b>策略</b> : <code>{html.escape(rule_name)}</code>", 15)

        elif command == "delrule":
            parts = args.split()
            if len(parts) < 2: return await safe_reply(event, f"❓ 语法: {pe}delrule 分组名 规则名 [正则]", 15)
            matched, _ = find_folder(state.folder_rules, parts[0].strip())
            if not matched: return await safe_reply(event, "❌ 找不到该管道", 15)
            rule_name, remove_words = parts[1].strip(), set(re.escape(w.strip()) for w in parts[2:] if w.strip())
            existing = state.folder_rules[matched].get("rules", {})
            if rule_name not in existing: return await safe_reply(event, "❌ 策略不存在", 15)
            current_words = set(t.strip() for t in existing[rule_name].strip("()").split("|") if t.strip())
            remain_words = current_words - remove_words
            if not remove_words or not remain_words:
                def do_delall(cfg): del cfg["folder_rules"][matched]["rules"][rule_name]
                edit_config(do_delall)
                write_biz_log("SYS", f"废弃策略模块: {rule_name}")
                return await apply_hot_reload(event, state, f"🗑️ <b>[ 策略模块已废弃 ]</b>", 15)
            new_pattern = "(" + "|".join(sorted(remain_words)) + ")"
            def do_update(cfg): cfg["folder_rules"][matched]["rules"][rule_name] = new_pattern
            edit_config(do_update)
            write_biz_log("SYS", f"剔除策略词汇: {rule_name}")
            await apply_hot_reload(event, state, f"✂️ <b>[ 策略单元已精准剥离 ]</b>", 15)

        elif command == "routes":
            lines = [f"  • <b>{html.escape(f)}</b> : <code>{html.escape(p)}</code>" for f, p in state.auto_route_rules.items()]
            block = "\n".join(lines) if lines else "  <i>(暂无智能路由策略)</i>"
            await safe_reply(event, f"🔀 <b>智能收纳路由表</b>\n\n{block}\n\n<i>配置指令: {pe}addroute 分组名 正则</i>", auto_delete=45)

        elif command == "addroute":
            parts = args.split(maxsplit=1)
            if len(parts) < 2: return await safe_reply(event, f"❓ 语法: {pe}addroute 分组名 匹配词1 [匹配词2...]", 15)
            folder_name, raw_pattern = parts[0].strip(), parts[1].strip()
            
            if " " in raw_pattern and "|" not in raw_pattern and not raw_pattern.startswith("^"):
                words = [re.escape(w) for w in raw_pattern.split() if w.strip()]
                regex = "(" + "|".join(words) + ")"
            else:
                regex = raw_pattern

            try: re.compile(regex)
            except Exception as e: return await safe_reply(event, f"❌ <b>规则编译失败</b>: {e}", 15)
            
            def do_addroute(cfg): cfg.setdefault("auto_route_rules", {})[folder_name] = regex
            edit_config(do_addroute)
            write_biz_log("SYS", f"挂载智能路由: {folder_name} -> {regex}")
            
            report = await auto_route_groups(client, {folder_name: regex})
            
            import sync_engine
            importlib.reload(sync_engine)
            cfg = _load_fresh_config()
            f_new, c_new, _, _ = await sync_engine.sync(client, cfg)
            cfg["folder_rules"], cfg["_system_cache"] = f_new, c_new
            _save_config(cfg)
            state.hot_reload(f_new, c_new, cfg.get("auto_route_rules", {}))

            msg = f"✅ <b>[ 智能路由已挂载 ]</b>\n▸ <b>目标分组</b> : <code>{html.escape(folder_name)}</code>\n\n🔍 <b>[ 队列状态 ]</b>"
            if folder_name in report["created"]:
                msg += f"\n✨ <b>无中生有</b>: 已为您自动建组并装入 {report['queued'].get(folder_name,0)} 个会话！排队慢速同步中。"
            elif folder_name in report["matched_zero"]:
                msg += "\n🔕 <b>零匹配</b>\n当前账号未发现任何匹配该规则的群组/频道。"
            else:
                queued = report["queued"].get(folder_name, 0)
                already = report["already_in"].get(folder_name, 0)
                if queued > 0: msg += f"\n⏳ <b>任务排队中</b>: {queued} 个匹配到的新群组已送入后台队列，将慢慢同步至TG以防限制。"
                if already > 0: msg += f" (另有 {already} 个群已存在该分组，已自动跳过)"

            await apply_hot_reload(event, state, msg, 25)

        elif command == "delroute":
            if not args: return await safe_reply(event, f"❓ 语法: {pe}delroute 分组名", 15)
            folder_name = args.strip()
            if folder_name not in state.auto_route_rules: return await safe_reply(event, "❌ 找不到该路由策略", 15)
            def do_delroute(cfg): del cfg["auto_route_rules"][folder_name]
            edit_config(do_delroute)
            write_biz_log("SYS", f"剔除智能路由: {folder_name}")
            await apply_hot_reload(event, state, f"🗑️ <b>[ 智能路由已剔除 ]</b>\n▸ <b>解绑分组</b> : <code>{html.escape(folder_name)}</code>", 15)

        elif command == "sync":
            await safe_reply(event, "🔄 <b>[ 拓扑云端全量同步 ]</b>\n> 正在执行扫描并投递后台队列...", auto_delete=0)
            import sync_engine
            importlib.reload(sync_engine)
            cfg = _load_fresh_config()
            
            report = await auto_route_groups(client, cfg.get("auto_route_rules", {}))
            f_new, c_new, has_changes, sync_report = await sync_engine.sync(client, cfg)
            
            cfg["folder_rules"], cfg["_system_cache"] = f_new, c_new
            _save_config(cfg)
            state.hot_reload(f_new, c_new, cfg.get("auto_route_rules", {}))
            
            if has_changes or report["queued"] or report["created"]:
                write_biz_log("SYNC", "触发同步：发现新变动，已送入后台队列处理")
            else:
                write_biz_log("SYNC", "触发同步：云端拓扑无实质变动")
                
            msg = "✅ <b>全盘扫描诊断完毕</b>\n"
            msg += f"▸ 共巡检了 {len(cfg.get('auto_route_rules', {}))} 条路由规则\n"
            
            if report["queued"] or report["created"] or report["matched_zero"] or report["errors"]:
                msg += "\n🔀 <b>[ 后台任务报告 ]</b>\n"
                for fn in report["created"]: msg += f"  ▸ <code>{html.escape(fn)}</code> : ✨ 新建分组排队中\n"
                for fn, cnt in report["queued"].items(): msg += f"  ▸ <code>{html.escape(fn)}</code> : ⏳ 补充 {cnt} 个群组排队中\n"
                for fn in report["matched_zero"]: msg += f"  ▸ <code>{html.escape(fn)}</code> : 🔍 正则零命中\n"
                for fn, err in report["errors"].items(): msg += f"  ▸ <code>{html.escape(fn)}</code> : ❌ API更新被拒\n"
                
            msg += "\n⚡ <b>(所有补充操作由后台慢速完成，绝对防限制)</b>"
            await safe_reply(event, msg, 25)

        elif command == "update":
            reply_msg = await event.edit("🔄 <b>[ OTA 固件拉取更新 ]</b>\n> 正在从主分支同步原生代码...")
            write_biz_log("SYS", "触发 OTA 固件拉取更新")
            if reply_msg:
                with open(os.path.join(WORK_DIR, ".last_msg"), "w") as f:
                    json.dump({"chat_id": "me", "msg_id": reply_msg.id, "action": "update"}, f)
            await asyncio.sleep(1)
            cmd = f"curl -fsSL https://github.com/chenmo8848/TG-Radar/archive/refs/heads/main.zip -o /tmp/tgr.zip && unzip -q -o /tmp/tgr.zip -d /tmp/ && cp -af /tmp/TG-Radar-main/. {WORK_DIR}/ && rm -rf /tmp/tgr.zip /tmp/TG-Radar-main && curl -fsSL https://api.github.com/repos/chenmo8848/TG-Radar/commits/main | python3 -c \"import sys,json; print(json.load(sys.stdin).get('sha',''))\" > {WORK_DIR}/.commit_sha"
            subprocess.run(cmd, shell=True)
            subprocess.Popen(["sudo", "systemctl", "restart", SERVICE_NAME])

        elif command == "restart":
            reply_msg = await event.edit("🔄 <b>[ 物理级系统重启 ]</b>\n正在通过 Systemd 重载守护进程...")
            write_biz_log("SYS", "触发守护进程重启")
            if reply_msg:
                with open(os.path.join(WORK_DIR, ".last_msg"), "w") as f:
                    json.dump({"chat_id": "me", "msg_id": reply_msg.id, "action": "restart"}, f)
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
                        write_biz_log("HIT", f"关键词: {match.group(0)} | 管道: {task['folder_name']} | 来源: {chat_title}")
                    except: pass
                    break
        except Exception as e:
            logger.error("消息解析流转异常: %s", e)

async def main():
    config = load_config()
    api_id, api_hash, global_alert, notify_channel, cmd_prefix, auto_route = validate_config(config)
    _save_config(config)

    state = AppState()
    state.global_alert = global_alert
    state.hot_reload(config.get("folder_rules", {}), config.get("_system_cache", {}), auto_route)

    async with TelegramClient(SESSION_NAME, api_id, api_hash) as client:
        client.parse_mode = 'html'
        
        asyncio.create_task(route_task_worker(client))
        
        async def internal_auto_sync():
            while True:
                await asyncio.sleep(1800)
                try:
                    cfg = _load_fresh_config()
                    route_report = await auto_route_groups(client, cfg.get("auto_route_rules", {}))
                    import sync_engine
                    importlib.reload(sync_engine)
                    f_new, c_new, changed, _ = await sync_engine.sync(client, cfg)
                    if changed or route_report["queued"] or route_report["created"]:
                        cfg["folder_rules"], cfg["_system_cache"] = f_new, c_new
                        _save_config(cfg)
                        state.hot_reload(f_new, c_new, cfg.get("auto_route_rules", {}))
                        logger.info("📡 内部巡检：已增补最新路由及拓扑热重载。")
                        write_biz_log("SYNC", "系统自动巡检：增量投递路由任务并完成热重载")
                except Exception as e:
                    logger.error("内部巡检异常: %s", e)

        asyncio.create_task(internal_auto_sync())
        register_handlers(client, state, notify_channel, cmd_prefix)
        await send_startup_notification(client, notify_channel, state, cmd_prefix)
        write_biz_log("SYS", "系统服务主进程启动完成")
        await client.run_until_disconnected()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
