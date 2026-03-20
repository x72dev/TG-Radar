import os, re, sys, json, asyncio, logging, subprocess, html, importlib, signal
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
SYNC_LOCK = asyncio.Lock()  

def get_mem_usage() -> str:
    try:
        import resource
        return f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.1f} MB"
    except: return "N/A"

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
    enabled_cnt = sum(1 for cfg in state.folder_rules.values() if cfg.get("enable", False))
    for name, cfg in state.folder_rules.items():
        if cfg.get("enable", False):
            grp_cnt = len(state.system_cache.get(name, []))
            rule_cnt = len(cfg.get("rules", {}))
            lines.append(f"🟢 <b>{html.escape(name)}</b> (监听了 {grp_cnt} 个群, 包含 {rule_cnt} 条规则)")
    folder_block = "\n".join(lines) if lines else "<i>(当前没有开启任何分组的监控)</i>"
    
    route_lines = []
    for f_name, pat in state.auto_route_rules.items():
        route_lines.append(f"🔀 将名含 <code>{html.escape(pat)}</code> 的群拉入 <code>{html.escape(f_name)}</code>")
    route_block = "\n".join(route_lines) if route_lines else "<i>(当前没有设置自动路由)</i>"
    
    msg = f"""📊 <b>TG-Radar 监控系统已上线</b>

<b>⚙️ 运行概况</b>
· 进程状态：<code>🟢 稳定运行中</code>
· 启动时间：<code>{state.start_time.strftime('%Y-%m-%d %H:%M:%S')}</code>
· 内存占用：<code>{get_mem_usage()}</code>

<b>🌐 监控规模</b>
· 活跃分组：<code>{enabled_cnt}</code> 个 (系统共记录 {len(state.folder_rules)} 个)
· 正在监听：<code>{len(state.target_map)}</code> 个活跃群组/频道
· 生效规则：<code>{state.valid_rules_count}</code> 条监控策略

<b>[ 正在监控的分组 ]</b>
<blockquote>{folder_block}</blockquote>

<b>[ 自动路由配置 ]</b>
<blockquote>{route_block}</blockquote>

💡 <i>需要管理系统？请发送 <code>{html.escape(cmd_prefix)}help</code> 查看所有指令。</i>"""
    
    last_msg_path = os.path.join(WORK_DIR, ".last_msg")
    target = notify_channel if notify_channel else "me"
    msg_obj = None

    if os.path.exists(last_msg_path):
        try:
            with open(last_msg_path, "r") as f: ctx = json.load(f)
            action = ctx.get("action", "restart")
            prefix_text = "✨ <b>[ 代码更新完毕 ]</b> 系统已加载最新版本。\n\n" if action == "update" else "🔄 <b>[ 重启任务完毕 ]</b> 系统进程已恢复。\n\n"
            msg_obj = await client.edit_message("me", ctx["msg_id"], prefix_text + msg)
            os.remove(last_msg_path)
            write_biz_log("SYS", f"系统恢复上线 (操作: {action})")
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
    final_text = f"{success_text}\n\n<i>✅ 设置已自动生效，无需重启。</i>"
    await safe_reply(event, final_text, auto_delete)

async def route_task_worker(client):
    while True:
        task = await ROUTE_QUEUE.get()
        try:
            await client(functions.messages.UpdateDialogFilterRequest(
                id=task['folder_id'],
                filter=task['folder_obj']
            ))
            write_biz_log("SYS", f"队列任务：向分组 [{task['name']}] 自动添加了 {task['cnt']} 个群组")
        except Exception as e:
            write_biz_log("ERR", f"队列异常：分组 [{task['name']}] 自动添加群组失败: {e}")
        finally:
            ROUTE_QUEUE.task_done()
        await asyncio.sleep(4)

async def auto_route_groups(client, auto_route_rules) -> dict:
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

            if len(target_folder.include_peers) + len(to_add) > 100:
                available = max(0, 100 - len(target_folder.include_peers))
                to_add = to_add[:available]

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
        logger.error(f"智能路由任务分配异常: {e}")
        
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
            try: await safe_reply(event, f"❌ <b>系统执行报错</b>\n<blockquote expandable>{html.escape(str(exc))}</blockquote>", 15)
            except: pass

    async def _dispatch(event, command: str, args: str):
        if command == "help":
            await safe_reply(event, f"""⚙️ <b>TG-Radar 管理菜单</b>
<i>请直接发送以下指令进行系统管理：</i>

<b>📊 运行状态查看</b>
<code>{pe}status</code> - 详细的系统监控大屏
<code>{pe}ping</code>   - 简单的系统心跳测试
<code>{pe}log 30</code> - 查看最近 30 条运行日志

<b>📂 监听分组管理</b>
<code>{pe}folders</code>   - 查看当前有几个分组、分别监控了多少群
<code>{pe}enable [名称]</code> - 开启某个分组的监控
<code>{pe}disable [名称]</code>- 关闭某个分组的监控
<code>{pe}rules [名称]</code>  - 查看某个分组里加了什么监控词

<b>🛡️ 监控词管理</b>
<code>{pe}addrule [分组名] [规则名] [关键词]</code> 
（例如：<code>{pe}addrule 业务群 核心词 苹果 华为</code>，直接用空格分隔关键词即可）
<code>{pe}delrule [分组名] [规则名] [要删的词]</code>

<b>🔀 智能路由配置 (自动把群加入分组)</b>
<code>{pe}routes</code>    - 查看现有的自动收纳规则
<code>{pe}addroute [分组名] [群名匹配词]</code>
（例如：<code>{pe}addroute 业务群 供需 担保</code>，群名带有供需或担保的会自动加进业务群）
<code>{pe}delroute [分组名]</code> - 删除自动收纳规则

<b>🔧 系统维护指令</b>
<code>{pe}sync</code>    - 强制执行一次全盘数据比对与同步
<code>{pe}update</code>  - 一键更新到最新版代码
<code>{pe}restart</code> - 重启系统进程

<i>(提示：为了防止面板刷屏，本条消息会在 45 秒后自动删除)</i>""", auto_delete=45)
            
        elif command == "ping": 
            await safe_reply(event, f"⚡ <b>系统运行正常</b> | 已经运行了: <code>{fmt_uptime(state.start_time)}</code> | 历史总计拦截: <code>{state.total_hits}</code> 次", auto_delete=10)
        
        elif command == "status":
            last = f"<code>{html.escape(state.last_hit_folder)}</code> <i>({fmt_dt(state.last_hit_time)})</i>" if state.last_hit_time else "暂无记录"
            enabled_cnt = sum(1 for cfg in state.folder_rules.values() if cfg.get("enable", False))
            
            queue_size = ROUTE_QUEUE.qsize()
            if queue_size > 0:
                q_info = f"\n· 队列任务：<code>有 {queue_size} 个后台补充任务正在缓慢执行中</code> ⏳"
            else:
                q_info = f"\n· 队列任务：<code>全部执行完毕 (当前空闲)</code> ✅"
            
            await safe_reply(event, f"""📊 <b>TG-Radar 详细监控大屏</b>

<b>⚙️ 核心运行状态</b>
· 系统状态：<code>🟢 稳定监控中</code>
· 内存占用：<code>{get_mem_usage()}</code>
· 持续运行：<code>{fmt_uptime(state.start_time)}</code>

<b>🌐 当前监控规模</b>
· 启用的分组：<code>{enabled_cnt}</code> 个 (系统内共记录了 {len(state.folder_rules)} 个分组)
· 正在监听的群：<code>{len(state.target_map)}</code> 个活跃群组/频道
· 已加载的规则：<code>{state.valid_rules_count}</code> 条监控策略

<b>🔀 智能路由调度 (声明式对齐引擎)</b>
· 自动收纳规则：已配置 <code>{len(state.auto_route_rules)}</code> 条收纳条件{q_info}

<b>🛡️ 拦截成果统计</b>
· 历史累计拦截：<code>{state.total_hits}</code> 次
· 最近一次命中：{last}

<i>(如需查看命中明细，请发送 <code>{pe}log</code> 指令调阅日志)</i>""", auto_delete=30)
            
        elif command == "log":
            try: await event.edit(f"⏳ <b>正在为您读取日志记录...</b>")
            except: pass
            n_lines = 20
            if args and args.isdigit(): n_lines = max(1, min(100, int(args)))
            try:
                if not os.path.exists(MONITOR_LOG_PATH):
                    return await safe_reply(event, "📋 <b>系统运行日志</b>\n\n<i>本地目前没有任何运行记录。</i>", 15)
                with open(MONITOR_LOG_PATH, "r", encoding="utf-8") as f: lines = f.readlines()
                lines_out = lines[-n_lines:]
                if not lines_out: return await safe_reply(event, "📋 <b>系统运行日志</b>\n\n<i>本地目前没有任何运行记录。</i>", 15)
                log_body = "".join(lines_out).strip()
                if len(log_body) > 3600: log_body = "…(前面的文本太长已省略)\n" + log_body[-3500:]
                html_msg = f"📋 <b>最近的 {len(lines_out)} 条运行日志</b>\n<blockquote expandable>{html.escape(log_body)}</blockquote>"
                await safe_reply(event, html_msg, auto_delete=60)
            except Exception as e:
                await safe_reply(event, f"❌ <b>读取日志失败</b>: <code>{e}</code>", 15)

        elif command == "folders":
            lines, enabled_cnt = [], 0
            for name, cfg in state.folder_rules.items():
                is_on = cfg.get("enable", False)
                rule_cnt, grp_cnt = len(cfg.get("rules", {})), len(state.system_cache.get(name, []))
                if is_on:
                    lines.append(f"🟢 <b>{html.escape(name)}</b>\n  └ 正在监控 <code>{grp_cnt}</code> 个群，包含 <code>{rule_cnt}</code> 条规则")
                    enabled_cnt += 1
                else: lines.append(f"⚪ <b>{html.escape(name)}</b> <i>(状态: 已关闭)</i>\n  └ 包含了 <code>{rule_cnt}</code> 条规则")
            body = "\n\n".join(lines) if lines else "<i>系统中目前没有获取到任何分组信息。</i>"
            await safe_reply(event, f"📂 <b>您的 TG 分组列表</b> (已开启监控: {enabled_cnt}个)\n\n{body}", auto_delete=45)

        elif command == "rules":
            if not args: return await safe_reply(event, f"⚠️ <b>请指定要查看的分组</b>\n示例: <code>{pe}rules 业务群</code>", 15)
            matched, _ = find_folder(state.folder_rules, args)
            if not matched: return await safe_reply(event, f"⚠️ <b>找不到分组</b>\n系统里没有找到名叫 <code>{html.escape(args)}</code> 的分组。", 15)
            cfg = state.folder_rules[matched]
            rules_block = "\n".join([f"· <b>{html.escape(lvl)}</b>\n  匹配词：<code>{html.escape(pat)}</code>" for i, (lvl, pat) in enumerate(cfg.get("rules", {}).items(), 1)]) if cfg.get("rules") else "  <i>(当前分组下没有设置任何规则)</i>"
            await safe_reply(event, f"🛡️ <b>【{html.escape(matched)}】内的监控规则</b>\n<blockquote>{rules_block}</blockquote>", auto_delete=45)

        elif command in ["enable", "disable"]:
            if SYNC_LOCK.locked():
                return await safe_reply(event, "⚠️ <b>系统正忙</b>\n后台正在执行配置同步，请稍后再试。", 15)
            async with SYNC_LOCK:
                if not args: return await safe_reply(event, f"⚠️ <b>请指定分组名称</b>\n示例: <code>{pe}{command} 业务群</code>", 15)
                matched, _ = find_folder(state.folder_rules, args)
                if not matched: return await safe_reply(event, "⚠️ 系统找不到您输入的这个分组名。", 15)
                tgt = (command == "enable")
                def do_toggle(cfg): cfg["folder_rules"][matched]["enable"] = tgt
                edit_config(do_toggle)
                write_biz_log("SYS", f"更改监控开关：{matched} -> {tgt}")
                status_txt = "🟢 已开启监控" if tgt else "⚪ 已关闭监控 (挂起)"
                await apply_hot_reload(event, state, f"⚙️ <b>设置已更新</b>\n分组 <code>{html.escape(matched)}</code> {status_txt}", 15)

        elif command == "addrule":
            if SYNC_LOCK.locked():
                return await safe_reply(event, "⚠️ <b>系统正忙</b>\n后台正在执行配置同步，请稍后再试。", 15)
            async with SYNC_LOCK:
                parts = args.split(maxsplit=2)
                if len(parts) < 3: return await safe_reply(event, f"⚠️ <b>缺少内容</b>\n请按照格式发送: <code>{pe}addrule [分组名] [规则名] [要监控的词]</code>", 15)
                matched, _ = find_folder(state.folder_rules, parts[0].strip())
                if not matched: return await safe_reply(event, "⚠️ 找不到您输入的这个分组。", 15)
                rule_name = parts[1].strip()
                new_words = [re.escape(w.strip()) for w in parts[2].split() if w.strip()]
                existing = state.folder_rules[matched].get("rules", {})
                current_words = set(t.strip() for t in existing.get(rule_name, "").strip("()").split("|") if t.strip())
                current_words.update(new_words)
                merged_pattern = "(" + "|".join(sorted(current_words)) + ")"
                def do_add(cfg): cfg["folder_rules"][matched].setdefault("rules", {})[rule_name] = merged_pattern
                edit_config(do_add)
                write_biz_log("SYS", f"添加监控词：{rule_name} -> {matched}")
                await apply_hot_reload(event, state, f"✅ <b>监控词添加成功</b>\n· 目标分组: <code>{html.escape(matched)}</code>\n· 规则名称: <code>{html.escape(rule_name)}</code>", 15)

        elif command == "delrule":
            if SYNC_LOCK.locked():
                return await safe_reply(event, "⚠️ <b>系统正忙</b>\n后台正在执行配置同步，请稍后再试。", 15)
            async with SYNC_LOCK:
                parts = args.split()
                if len(parts) < 2: return await safe_reply(event, f"⚠️ <b>缺少内容</b>\n请按照格式发送: <code>{pe}delrule [分组名] [规则名] [要删的词]</code>", 15)
                matched, _ = find_folder(state.folder_rules, parts[0].strip())
                if not matched: return await safe_reply(event, "⚠️ 找不到您输入的这个分组。", 15)
                rule_name, remove_words = parts[1].strip(), set(re.escape(w.strip()) for w in parts[2:] if w.strip())
                existing = state.folder_rules[matched].get("rules", {})
                if rule_name not in existing: return await safe_reply(event, "⚠️ 这个分组里没有叫这个名字的规则。", 15)
                current_words = set(t.strip() for t in existing[rule_name].strip("()").split("|") if t.strip())
                remain_words = current_words - remove_words
                if not remove_words or not remain_words:
                    def do_delall(cfg): del cfg["folder_rules"][matched]["rules"][rule_name]
                    edit_config(do_delall)
                    write_biz_log("SYS", f"删除了整个规则：{rule_name}")
                    return await apply_hot_reload(event, state, f"🗑️ <b>已彻底删除整条规则</b>\n· 目标分组: <code>{html.escape(matched)}</code>\n· 规则名称: <code>{html.escape(rule_name)}</code>", 15)
                new_pattern = "(" + "|".join(sorted(remain_words)) + ")"
                def do_update(cfg): cfg["folder_rules"][matched]["rules"][rule_name] = new_pattern
                edit_config(do_update)
                write_biz_log("SYS", f"移除了部分监控词：{rule_name}")
                await apply_hot_reload(event, state, f"✂️ <b>指定的监控词已删除</b>\n· 目标分组: <code>{html.escape(matched)}</code>\n· 规则名称: <code>{html.escape(rule_name)}</code>", 15)

        elif command == "routes":
            lines = [f"· 自动归入：<code>{html.escape(f)}</code>\n  群名包含：<code>{html.escape(p)}</code>" for f, p in state.auto_route_rules.items()]
            block = "\n\n".join(lines) if lines else "<i>(当前没有设置任何自动收纳规则)</i>"
            await safe_reply(event, f"🔀 <b>自动路由 (群组收纳) 列表</b>\n<blockquote>{block}</blockquote>", auto_delete=45)

        elif command == "addroute":
            if SYNC_LOCK.locked():
                return await safe_reply(event, "⚠️ <b>系统正忙</b>\n后台正在执行其他配置同步，请稍等一两秒后再试。", 15)
            
            async with SYNC_LOCK:
                parts = args.split(maxsplit=1)
                if len(parts) < 2: return await safe_reply(event, f"⚠️ <b>内容不完整</b>\n正确格式: <code>{pe}addroute [要存入的分组名] [群名匹配词]</code>", 15)
                folder_name, raw_pattern = parts[0].strip(), parts[1].strip()
                
                if " " in raw_pattern and "|" not in raw_pattern and not raw_pattern.startswith("^"):
                    words = [re.escape(w) for w in raw_pattern.split() if w.strip()]
                    regex = "(" + "|".join(words) + ")"
                else:
                    regex = raw_pattern

                try: re.compile(regex)
                except Exception as e: return await safe_reply(event, f"❌ <b>词汇格式有误</b>: <code>{e}</code>", 15)
                
                def do_addroute(cfg): cfg.setdefault("auto_route_rules", {})[folder_name] = regex
                edit_config(do_addroute)
                write_biz_log("SYS", f"添加自动收纳规则: {folder_name}")
                
                report = await auto_route_groups(client, {folder_name: regex})
                
                import sync_engine
                importlib.reload(sync_engine)
                fresh_cfg = _load_fresh_config()
                f_new, c_new, _, _ = await sync_engine.sync(client, fresh_cfg)
                fresh_cfg["folder_rules"], fresh_cfg["_system_cache"] = f_new, c_new
                _save_config(fresh_cfg)
                state.hot_reload(f_new, c_new, fresh_cfg.get("auto_route_rules", {}))

                msg = f"✅ <b>自动收纳规则已保存</b>\n凡是满足条件的群，都会被存入 <code>{html.escape(folder_name)}</code> 分组。\n\n<b>🔍 刚刚为您执行了一次全盘扫描：</b>\n"
                if folder_name in report["created"]:
                    msg += f"· 💡 <b>为您新建了分组</b>: 系统发现您原来没建这个分组，已经帮您建好了，并找到了 <code>{report['queued'].get(folder_name,0)}</code> 个群交给了后台。\n"
                elif folder_name in report["matched_zero"]:
                    msg += "· 🔕 <b>没有匹配到群</b>: 翻遍了您的账号，没有找到名字里包含这些词的群组。\n"
                else:
                    queued = report["queued"].get(folder_name, 0)
                    already = report["already_in"].get(folder_name, 0)
                    if queued > 0: msg += f"· ⏳ <b>排队添加中</b>: 找到了 <code>{queued}</code> 个需要加入的群。为了防封号，系统正在后台排队缓慢添加，请稍后查看。\n"
                    if already > 0: msg += f"· ✅ <b>跳过已有群</b>: 有 <code>{already}</code> 个群本来就在这个分组里，已为您自动跳过。\n"
                    
                msg += f"\n<i>(控制台已解除锁定，您可以继续操作)</i>"

                await safe_reply(event, msg, 25)

        elif command == "delroute":
            if SYNC_LOCK.locked():
                return await safe_reply(event, "⚠️ <b>系统正忙</b>\n后台正在执行配置同步，请稍后再试。", 15)
            async with SYNC_LOCK:
                if not args: return await safe_reply(event, f"⚠️ <b>参数缺失</b>: <code>{pe}delroute [分组名]</code>", 15)
                folder_name = args.strip()
                if folder_name not in state.auto_route_rules: return await safe_reply(event, "⚠️ 没有找到这条自动收纳规则。", 15)
                def do_delroute(cfg): del cfg["auto_route_rules"][folder_name]
                edit_config(do_delroute)
                write_biz_log("SYS", f"删除了收纳规则: {folder_name}")
                await apply_hot_reload(event, state, f"🗑️ <b>收纳规则已删除</b>\n以后将不再自动往 <code>{html.escape(folder_name)}</code> 里加群了。", 15)

        elif command == "sync":
            if SYNC_LOCK.locked():
                return await safe_reply(event, "⚠️ <b>系统正忙</b>\n后台正在执行其他同步任务，请稍等一两秒后再试。", 15)
            
            async with SYNC_LOCK:
                await safe_reply(event, f"⏳ <b>系统正在扫描全局差异...</b>", auto_delete=0)
                import sync_engine
                importlib.reload(sync_engine)
                cfg = _load_fresh_config()
                
                report = await auto_route_groups(client, cfg.get("auto_route_rules", {}))
                    
                f_new, c_new, has_changes, sync_report = await sync_engine.sync(client, cfg)
                
                fresh_cfg = _load_fresh_config()
                fresh_cfg["folder_rules"], fresh_cfg["_system_cache"] = f_new, c_new
                _save_config(fresh_cfg)
                state.hot_reload(f_new, c_new, fresh_cfg.get("auto_route_rules", {}))
                
                if has_changes or report["queued"] or report["created"]:
                    write_biz_log("SYNC", "执行了数据同步，发现变动")
                else:
                    write_biz_log("SYNC", "数据同步完毕，一切正常")
                    
                msg = f"✅ <b>TG 最新数据已核准完毕</b>\n\n"
                msg += f"· 系统同时检查了 <code>{len(cfg.get('auto_route_rules', {}))}</code> 条自动收纳规则。\n"
                
                if report["queued"] or report["created"] or report["matched_zero"] or report["errors"]:
                    msg += "\n<b>[ 自动收纳任务扫描结果 ]</b>\n<blockquote>"
                    for fn in report["created"]: msg += f"· {html.escape(fn)} : ✨ 为您自动新建了该分组\n"
                    for fn, cnt in report["queued"].items(): msg += f"· {html.escape(fn)} : ⏳ 找到了 {cnt} 个缺失的群，已排队等待添加\n"
                    for fn in report["matched_zero"]: msg += f"· {html.escape(fn)} : 🔕 没找到符合名字的群\n"
                    for fn, err in report["errors"].items(): msg += f"· {html.escape(fn)} : ❌ 接口提示错误\n"
                    msg += "</blockquote>\n"
                    msg += f"<i>(控制台已解除锁定，所有添加操作均在后台静默完成)</i>"
                    
                await safe_reply(event, msg, 25)

        elif command == "update":
            reply_msg = await event.edit("🔄 <b>正在获取最新版程序...</b>\n<i>(系统将立即执行热重启，未完队列将在开机后由引擎接管)</i>")
            write_biz_log("SYS", "开始进行一键更新")
            if reply_msg:
                with open(os.path.join(WORK_DIR, ".last_msg"), "w") as f:
                    json.dump({"chat_id": "me", "msg_id": reply_msg.id, "action": "update"}, f)
            await asyncio.sleep(1)
            cmd = f"curl -fsSL https://github.com/chenmo8848/TG-Radar/archive/refs/heads/main.zip -o /tmp/tgr.zip && unzip -q -o /tmp/tgr.zip -d /tmp/ && cp -af /tmp/TG-Radar-main/. {WORK_DIR}/ && rm -rf /tmp/tgr.zip /tmp/TG-Radar-main && curl -fsSL https://api.github.com/repos/chenmo8848/TG-Radar/commits/main | python3 -c \"import sys,json; print(json.load(sys.stdin).get('sha',''))\" > {WORK_DIR}/.commit_sha"
            subprocess.run(cmd, shell=True)
            subprocess.Popen(["sudo", "systemctl", "restart", SERVICE_NAME])

        elif command == "restart":
            reply_msg = await event.edit("🔄 <b>系统即将重启...</b>\n<i>(未完任务将在开机后由引擎自动接管)</i>")
            write_biz_log("SYS", "用户执行了重启系统")
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
            already_alerted = False
            
            for task in state.target_map[event.chat_id]:
                if already_alerted: break
                for level, pattern in task["rules"].items():
                    match = pattern.search(msg_text)
                    if not match: continue
                    if not sender_loaded:
                        sender_loaded = True
                        chat = await event.get_chat()
                        chat_title = getattr(chat, "title", "未知聊天")
                        try:
                            sender = await event.get_sender()
                            if getattr(sender, "bot", False): return
                            sender_name = getattr(sender, "username", "") or getattr(sender, "first_name", "") or "隐藏用户"
                        except: sender_name = "广播系统"
                    
                    preview = html.escape(msg_text[:1000])
                    msg_link = build_msg_link(chat, event.chat_id, event.id)
                    
                    alert_text = f"""🚨 <b>监控词触发提醒</b>

· <b>触发词汇</b>：<code>{html.escape(match.group(0))}</code>
· <b>所属规则</b>：<code>{html.escape(level)}</code> ({html.escape(task['folder_name'])})
· <b>来自哪里</b>：<code>{html.escape(chat_title)}</code>
· <b>发送人员</b>：@{html.escape(sender_name)}

<b>[ 详细文本内容 ]</b>
<blockquote expandable>{preview}</blockquote>"""
                    if msg_link: alert_text += f'\n🔗 <a href="{msg_link}">点击跳转查看原消息</a>'
                    try:
                        await client.send_message(task["alert_channel"], alert_text, link_preview=False)
                        state.total_hits += 1
                        state.last_hit_folder = task["folder_name"]
                        state.last_hit_time = datetime.now()
                        write_biz_log("HIT", f"拦截到了: {match.group(0)} | 来源群组: {chat_title}")
                        already_alerted = True
                    except: pass
                    break
        except Exception as e:
            logger.error("消息处理发生错误: %s", e)

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
            await asyncio.sleep(5)
            while True:
                try:
                    async with SYNC_LOCK:
                        cfg = _load_fresh_config()
                        route_report = await auto_route_groups(client, cfg.get("auto_route_rules", {}))
                        
                        import sync_engine
                        importlib.reload(sync_engine)
                        f_new, c_new, changed, _ = await sync_engine.sync(client, cfg)
                        
                        if changed or route_report["queued"] or route_report["created"]:
                            fresh_cfg = _load_fresh_config()
                            fresh_cfg["folder_rules"], fresh_cfg["_system_cache"] = f_new, c_new
                            _save_config(fresh_cfg)
                            state.hot_reload(f_new, c_new, fresh_cfg.get("auto_route_rules", {}))
                            logger.info("系统：定时环境同步已完成。")
                            write_biz_log("SYNC", "后台自动自检：配置已对齐")
                except Exception as e:
                    logger.error("底层轮询出错: %s", e)
                
                await asyncio.sleep(1800)

        asyncio.create_task(internal_auto_sync())
        register_handlers(client, state, notify_channel, cmd_prefix)
        await send_startup_notification(client, notify_channel, state, cmd_prefix)
        write_biz_log("SYS", "程序已成功启动并开始运行")
        
        await client.run_until_disconnected() 

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
