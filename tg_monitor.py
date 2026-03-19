import os, re, sys, json, asyncio, logging, subprocess, html
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from telethon import TelegramClient, events

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(WORK_DIR, "config.json")
SESSION_NAME = os.path.join(WORK_DIR, "TG_Radar_session")
SERVICE_NAME = "tg_monitor"
VERSION = "5.1.1"

@dataclass
class RadarStats:
    start_time: datetime = field(default_factory=datetime.now)
    total_hits: int = 0
    last_hit_folder: str = ""
    last_hit_time: Optional[datetime] = None

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

def validate_config(config: dict) -> tuple:
    api_id = config.get("api_id")
    api_hash = config.get("api_hash")
    if not api_id or not api_hash or api_id == 1234567:
        logger.error("引擎点火失败：未配置有效的 API 凭证 (api_id/api_hash)。")
        sys.exit(1)
    global_alert = config.get("global_alert_channel_id")
    notify_channel = config.get("notify_channel_id") or global_alert
    cmd_prefix = str(config.get("cmd_prefix") or "-")
    return int(api_id), str(api_hash), global_alert, notify_channel, cmd_prefix

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

async def send_notify(client, notify_channel, text: str, label: str = "notification"):
    target = notify_channel if notify_channel else "me"
    try: await client.send_message(target, text, link_preview=False)
    except: pass

async def send_startup_notification(client, notify_channel, target_map, valid_rules_count, folder_rules, system_cache, cmd_prefix):
    lines = []
    for name, cfg in folder_rules.items():
        if cfg.get("enable", False):
            grp_cnt = len(system_cache.get(name, []))
            rule_cnt = len(cfg.get("rules", {}))
            lines.append(f"  ✅ <code>{html.escape(name)}</code> · {grp_cnt} 节点 · {rule_cnt} 策略")
    folder_block = "\n".join(lines) if lines else "  _(暂无活跃的监听拓扑)_"
    msg = f"""🚀 <b>TG-Radar 态势感知引擎已上线</b> · <code>v{VERSION}</code>
━━━━━━━━━━━━━━━━━━━━━
📡 <b>监控矩阵</b> · <code>{len(target_map)}</code> 节点
🛡️ <b>防护策略</b> · <code>{valid_rules_count}</code> 规则
🕐 <b>启动时间</b> · <code>{datetime.now().strftime('%m-%d %H:%M:%S')}</code>
━━━━━━━━━━━━━━━━━━━━━
<b>[ 活跃管道 ]</b>
{folder_block}
━━━━━━━━━━━━━━━━━━━━━
💡 向此发送 <code>{html.escape(cmd_prefix)}help</code> 呼出核心控制台"""
    await send_notify(client, None, msg, "startup")

def _load_fresh_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f: return json.load(f)

def _save_config(cfg: dict) -> None:
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(cfg, f, indent=4, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)

def edit_config(modifier_fn) -> tuple:
    try:
        cfg = _load_fresh_config()
        summary = modifier_fn(cfg)
        _save_config(cfg)
        return True, summary
    except Exception as e: return False, str(e)

def find_folder(folder_rules: dict, query: str) -> tuple:
    if query in folder_rules: return query, []
    for name in folder_rules:
        if name.lower() == query.lower(): return name, []
    candidates = [n for n in folder_rules if query.lower() in n.lower()]
    return (None, candidates) if candidates else (None, list(folder_rules.keys()))

async def apply_and_restart(event, success_text: str) -> None:
    final_text = f"{success_text}\n━━━━━━━━━━━━━━━━━━━━━\n🔄 <b>触发守护进程重启...</b>\n⏳ 引擎正在后台进行静默热重载。"
    try: await event.edit(final_text)
    except: await event.reply(final_text)
    await asyncio.sleep(1.5)
    open(os.path.join(WORK_DIR, ".silent_start"), "w").close()
    subprocess.Popen(["sudo", "systemctl", "restart", SERVICE_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def register_handlers(client, target_map, valid_rules_count, stats, folder_rules, system_cache, notify_channel, global_alert, cmd_prefix) -> None:
    p = cmd_prefix
    pe = html.escape(p)
    cmd_regex = re.compile(rf"^{re.escape(p)}(\w+)[ \t]*([\s\S]*)", re.IGNORECASE)
    _cmd_chats = ["me"]
    if notify_channel: _cmd_chats.append(notify_channel)
    if global_alert and global_alert not in _cmd_chats: _cmd_chats.append(global_alert)
    for _fcfg in folder_rules.values():
        _ch = _fcfg.get("alert_channel_id")
        if _ch and _ch not in _cmd_chats: _cmd_chats.append(_ch)

    async def _respond(event, text: str, auto_delete: int = 0):
        msg = None
        try: msg = await event.edit(text)
        except:
            try: msg = await event.reply(text)
            except: return
        if msg and auto_delete > 0:
            async def schedule_delete():
                await asyncio.sleep(auto_delete)
                try: await msg.delete()
                except: pass
            asyncio.create_task(schedule_delete())

    async def _thinking(event, label: str = "处理中"):
        try: await event.edit(f"⏳ <b>{label}...</b>")
        except: pass

    @client.on(events.NewMessage(chats=_cmd_chats, pattern=cmd_regex))
    async def control_panel(event):
        command = event.pattern_match.group(1).lower()
        args = (event.pattern_match.group(2) or "").strip()
        logger.info("📡 接收到控制台指令: %s%s | 参数: %r", p, command, args[:80])
        try: await _dispatch(event, command, args)
        except Exception as exc:
            logger.error("❌ 指令 [%s] 执行引发底层崩溃: %s", command, exc, exc_info=True)
            try: await _respond(event, f"❌ <b>引擎内部异常</b>：<code>{html.escape(str(exc))}</code>")
            except: pass

    async def _dispatch(event, command: str, args: str):
        if command == "help":
            await _respond(event, f"""🤖 <b>TG-Radar 控制台</b> · <code>v{VERSION}</code>

<b>[ 态势观测 ]</b>
<code>{p}ping</code> 心跳探测
<code>{p}status</code> 监控大屏
<code>{p}log [n]</code> 系统日志

<b>[ 策略管理 ]</b>
<code>{p}folders</code> 活跃管道
<code>{p}rules &lt;分组&gt;</code> 策略明细
<code>{p}enable &lt;分组&gt;</code> 唤醒管道
<code>{p}disable &lt;分组&gt;</code> 休眠管道
<code>{p}addrule &lt;分组&gt; &lt;规则&gt; &lt;词&gt;</code> 追加
<code>{p}delrule &lt;分组&gt; &lt;规则&gt; [词]</code> 剔除
<code>{p}setalert &lt;分组&gt; &lt;频道&gt;</code> 专属告警
<code>{p}setglobal &lt;频道&gt;</code> 全局告警

<b>[ 系统底层 ]</b>
<code>{p}sync</code> 云端同步
<code>{p}restart</code> 热重启""", auto_delete=60)
            
        elif command == "ping": await _respond(event, f"🟢 <b>SYS.PING</b> | UP: <code>{fmt_uptime(stats.start_time)}</code> | 捕获量: <code>{stats.total_hits}</code>", auto_delete=10)
        elif command == "status":
            last = f"<code>{html.escape(stats.last_hit_folder)}</code> ({fmt_dt(stats.last_hit_time)})" if stats.last_hit_time else "暂无记录"
            enabled_cnt = sum(1 for cfg in folder_rules.values() if cfg.get("enable", False))
            await _respond(event, f"""⚡ <b>TG-Radar 监控大屏</b>

<b>[ 引擎状态 ]</b>
▸ 核心版本 : <code>v{VERSION}</code>
▸ 运行时长 : <code>{fmt_uptime(stats.start_time)}</code>

<b>[ 拓扑矩阵 ]</b>
▸ 监听节点 : <code>{len(target_map)}</code> 个群组
▸ 生效策略 : <code>{valid_rules_count}</code> 条规则
▸ 活跃管道 : <code>{enabled_cnt}/{len(folder_rules)}</code> 个分组

<b>[ 流量探测 ]</b>
▸ 累计拦截 : <code>{stats.total_hits}</code> 次命中
▸ 最新捕获 : {last}""", auto_delete=20)
            
        elif command == "log":
            await _thinking(event, "获取日志")
            n_lines = 20
            if args:
                try: n_lines = max(1, min(100, int(args)))
                except ValueError: return await _respond(event, f"❌ 行数参数无效：`{args}`")
            try:
                import html as _html
                import re as _re
                raw = subprocess.check_output(
                    ["journalctl", "-u", SERVICE_NAME, f"-n{n_lines}", "--no-pager", "--output=short-iso"],
                    text=True, stderr=subprocess.STDOUT
                )
                lines_out = []
                for line in raw.splitlines():
                    if line.startswith("--") or not line.strip(): continue
                    msg = line.split("]: ", 1)[-1] if "]: " in line else line
                    try:
                        m = _re.match(r"^\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2}) \[(\w+)\] (.*)", msg.strip())
                        if m:
                            time_str, level, msg_content = m.groups()
                            icon = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌", "DEBUG": "🔍"}.get(level, "·")
                            _trans = [("Radar started", "雷达启动"), ("groups,", "个群组，"), ("rules, prefix", "条规则，前缀"), 
                                      ("Listening --", "监听中"), ("send.*help.*Saved Messages", "向 Saved Messages 发送指令"), 
                                      ("Connecting to", "连接到"), ("Connection to.*complete", "连接成功"), ("Hit pushed", "命中推送")]
                            for en, zh in _trans: msg_content = _re.sub(en, zh, msg_content, flags=_re.IGNORECASE)
                            lines_out.append(f"{icon} <code>{time_str}</code> {_html.escape(msg_content)}")
                        else:
                            if len(msg) < 200 and not any(x in msg for x in ["Connecting to", "TcpFull"]):
                                lines_out.append(f"· {_html.escape(msg)}")
                    except Exception as parse_e:
                        lines_out.append(f"· {_html.escape(msg)} (解析异常: {parse_e})")
                
                if not lines_out: return await _respond(event, "📜 <b>日志</b> · 暂无可读记录")
                log_body = "\n".join(lines_out)
                if len(log_body) > 3600: log_body = "…（已截断）\n" + log_body[-3500:]
                
                # 【组装包含 expandable 属性的 HTML，并强制使用 HTML 解析器回复】
                html_msg = f"📜 <b>系统日志</b> · 最新 {n_lines} 条\n<blockquote expandable>{log_body}</blockquote>"
                try: await event.edit(html_msg, parse_mode='html')
                except: await event.reply(html_msg, parse_mode='html')
                
            except Exception as e:
                await _respond(event, f"❌ 获取日志失败: `{e}`")

        elif command == "folders":
            lines, enabled_cnt = [], 0
            for name, cfg in folder_rules.items():
                is_on = cfg.get("enable", False)
                rule_cnt, grp_cnt = len(cfg.get("rules", {})), len(system_cache.get(name, []))
                ch_str = f"路由ID <code>{cfg.get('alert_channel_id')}</code>" if cfg.get("alert_channel_id") else "<i>(全局默认路由)</i>"
                if is_on:
                    lines.append(f"✅ <b>{html.escape(name)}</b>\n   └ {grp_cnt} 节点 · {rule_cnt} 策略 · {ch_str}")
                    enabled_cnt += 1
                else: lines.append(f"⭕ {html.escape(name)}\n   └ {rule_cnt} 策略 · <i>(已休眠)</i>")
            body = "\n\n".join(lines) if lines else f"<i>尚未建立拓扑，请执行 <code>{pe}sync</code></i>"
            await _respond(event, f"📂 <b>数据管道拓扑矩阵</b> | 共 <code>{len(folder_rules)}</code> 组，活跃 <code>{enabled_cnt}</code> 组\n\n{body}\n\n<i>发送 <code>{pe}rules &lt;分组名&gt;</code> 查看策略明细</i>")

        elif command == "rules":
            if not args: return await _respond(event, f"❓ <b>语法</b>: <code>{pe}rules &lt;分组名&gt;</code>")
            matched, candidates = find_folder(folder_rules, args)
            if not matched: return await _respond(event, f"❌ 未找到匹配的数据管道 <code>{html.escape(args)}</code>")
            cfg = folder_rules[matched]
            rules, is_on = cfg.get("rules", {}), "✅ 运行中" if cfg.get("enable", False) else "⭕ 已休眠"
            ch_str = f"<code>{cfg.get('alert_channel_id')}</code>" if cfg.get("alert_channel_id") else "<i>(全局路由)</i>"
            rules_block = "\n\n".join([f"  {i}. <b>{html.escape(level)}</b>\n     <code>{html.escape(pattern)}</code>" for i, (level, pattern) in enumerate(rules.items(), 1)]) if rules else "  <i>(尚未挂载规则)</i>"
            await _respond(event, f"📋 <b>{html.escape(matched)}</b> | {is_on} | {ch_str} | <code>{len(system_cache.get(matched, []))}</code> 节点\n\n{rules_block}\n\n<i>当前已挂载 <code>{len(rules)}</code> 条子策略</i>")

        elif command in ["enable", "disable"]:
            if not args: return await _respond(event, f"❓ <b>语法</b>: <code>{pe}{command} &lt;分组名&gt;</code>")
            matched, _ = find_folder(folder_rules, args)
            if not matched: return await _respond(event, f"❌ 找不到该管道 <code>{html.escape(args)}</code>")
            target_state = (command == "enable")
            if folder_rules[matched].get("enable", False) == target_state: return await _respond(event, f"ℹ️ 管道 <code>{html.escape(matched)}</code> 已处于该状态。")
            def do_toggle(cfg): cfg["folder_rules"][matched]["enable"] = target_state; return ""
            edit_config(do_toggle)
            await apply_and_restart(event, f"{'✅' if target_state else '⭕'} <b>已成功{'唤醒' if target_state else '休眠'}数据管道</b> <code>{html.escape(matched)}</code>")

        elif command == "setalert":
            parts = args.split()
            if len(parts) != 2: return await _respond(event, f"❓ <b>语法</b>: <code>{pe}setalert &lt;分组&gt; &lt;频道ID&gt;</code>")
            try: channel_id = int(parts[1].strip())
            except: return await _respond(event, "❌ 频道 ID 必须为数字格式")
            matched, _ = find_folder(folder_rules, parts[0].strip())
            if not matched: return await _respond(event, "❌ 匹配不到对应数据管道")
            def do_setalert(cfg): cfg["folder_rules"][matched]["alert_channel_id"] = channel_id; return ""
            edit_config(do_setalert)
            await apply_and_restart(event, f"📢 <b>[ 独立告警路由已映射 ]</b>\n▸ <b>目标管道</b> : <code>{html.escape(matched)}</code>\n▸ <b>流转地址</b> : <code>{channel_id}</code>")

        elif command == "setglobal":
            try: channel_id = int(args.strip())
            except: return await _respond(event, "❌ 频道 ID 必须为数字格式")
            def do_setglobal(cfg): cfg["global_alert_channel_id"] = channel_id; return ""
            edit_config(do_setglobal)
            await apply_and_restart(event, f"🌐 <b>[ 全局默认路由已更新 ]</b>\n▸ <b>流转地址</b> : <code>{channel_id}</code>")

        elif command == "addrule":
            parts = args.split()
            if len(parts) < 3: return await _respond(event, f"❓ <b>语法</b>: <code>{pe}addrule &lt;分组&gt; &lt;规则名&gt; &lt;词汇1&gt; [词汇2...]</code>")
            matched, _ = find_folder(folder_rules, parts[0].strip())
            if not matched: return await _respond(event, "❌ 找不到该管道")
            rule_name = parts[1].strip()
            new_words = [re.escape(w.strip()) for w in parts[2:] if w.strip()]
            existing = folder_rules[matched].get("rules", {})
            current_words = set(t.strip() for t in existing.get(rule_name, "").strip("()").split("|") if t.strip())
            current_words.update(new_words)
            merged_pattern = "(" + "|".join(sorted(current_words)) + ")"
            try: re.compile(merged_pattern, re.IGNORECASE)
            except re.error as e: return await _respond(event, f"❌ <b>正则编译失败</b>: {html.escape(str(e))}")
            def do_add(cfg): cfg["folder_rules"][matched].setdefault("rules", {})[rule_name] = merged_pattern; return ""
            edit_config(do_add)
            await apply_and_restart(event, f"✅ <b>[ 监控策略已智能挂载 ]</b>\n▸ <b>从属管道</b> : <code>{html.escape(matched)}</code>\n▸ <b>策略标识</b> : <code>{html.escape(rule_name)}</code>")

        elif command == "delrule":
            parts = args.split()
            if len(parts) < 2: return await _respond(event, f"❓ <b>语法</b>: <code>{pe}delrule &lt;分组&gt; &lt;规则名&gt; [精确词汇...]</code>")
            matched, _ = find_folder(folder_rules, parts[0].strip())
            if not matched: return await _respond(event, "❌ 找不到该管道")
            rule_name, remove_words = parts[1].strip(), set(re.escape(w.strip()) for w in parts[2:] if w.strip())
            existing = folder_rules[matched].get("rules", {})
            if rule_name not in existing: return await _respond(event, "❌ 该策略未建立或不存在")
            current_words = set(t.strip() for t in existing[rule_name].strip("()").split("|") if t.strip())
            remain_words = current_words - remove_words
            if not remove_words or not remain_words:
                def do_delall(cfg): del cfg["folder_rules"][matched]["rules"][rule_name]; return ""
                edit_config(do_delall)
                return await apply_and_restart(event, f"🗑️ <b>[ 策略模块已整体废弃 ]</b>\n▸ <b>从属管道</b> : <code>{html.escape(matched)}</code>\n▸ <b>策略标识</b> : <code>{html.escape(rule_name)}</code>")
            new_pattern = "(" + "|".join(sorted(remain_words)) + ")"
            def do_update(cfg): cfg["folder_rules"][matched]["rules"][rule_name] = new_pattern; return ""
            edit_config(do_update)
            await apply_and_restart(event, f"✂️ <b>[ 策略单元已精准剥离 ]</b>\n▸ <b>从属管道</b> : <code>{html.escape(matched)}</code>\n▸ <b>策略标识</b> : <code>{html.escape(rule_name)}</code>")

        elif command == "restart":
            await event.reply("🔄 <b>[ 态势感知引擎热重启 ]</b>\n正在通过 Systemd 重载系统级守护进程...")
            await asyncio.sleep(1.0)
            subprocess.Popen(["sudo", "systemctl", "restart", SERVICE_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        elif command == "sync":
            await event.reply("🔄 <b>[ 拓扑云端全量同步 ]</b>\n> 正在执行原生底层无缝抓取...")
            try:
                import sync_engine
                current_cfg = _load_fresh_config()
                t0 = datetime.now()
                folder_rules_new, new_cache, has_changes, report = await sync_engine.sync(client, current_cfg)
                if has_changes:
                    current_cfg["folder_rules"], current_cfg["_system_cache"] = folder_rules_new, new_cache
                    _save_config(current_cfg)
                elapsed = (datetime.now() - t0).total_seconds()
                await sync_engine.send_sync_report(client, notify_channel, report, elapsed, p)
                if has_changes:
                    await asyncio.sleep(1.0)
                    open(os.path.join(WORK_DIR, ".silent_start"), "w").close()
                    subprocess.Popen(["sudo", "systemctl", "restart", SERVICE_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                logger.error("❌ 云端拓扑同步流转失败: %s", e, exc_info=True)
                await event.reply(f"❌ <b>流转失败</b>: <code>{html.escape(str(e))}</code>")

    @client.on(events.NewMessage)
    async def message_handler(event):
        try:
            if not (event.is_group or event.is_channel) or event.chat_id not in target_map: return
            msg_text = event.raw_text
            if not msg_text: return
            chat, chat_title, sender_name, sender_loaded = None, "", "", False
            for task in target_map[event.chat_id]:
                for level, pattern in task["rules"].items():
                    match = pattern.search(msg_text)
                    if not match: continue
                    hit_word = match.group(0)
                    if not sender_loaded:
                        sender_loaded = True
                        chat = await event.get_chat()
                        chat_title = getattr(chat, "title", "未知链路")
                        try:
                            sender = await event.get_sender()
                            if getattr(sender, "bot", False): return
                            sender_name = getattr(sender, "username", "") or getattr(sender, "first_name", "") or "隐藏域载体"
                        except: sender_name = "公海信道"
                    
                    preview = html.escape(msg_text[:1000] + ("..." if len(msg_text) > 1000 else ""))
                    msg_link = build_msg_link(chat, event.chat_id, event.id)
                    now_str = datetime.now().strftime("%H:%M:%S")
                    link_line = f'\n🔗 <a href="{msg_link}">直达情报源核心现场</a>' if msg_link else ""
                    
                    alert_text = f"""🚨 <b>[ 情报雷达告警 ]</b>

🎯 <b>触发关键词</b> : <code>{html.escape(hit_word)}</code>
🏷️ <b>命中策略</b> : <code>{html.escape(level)}</code> ({html.escape(task['folder_name'])})
📡 <b>情报来源</b> : <code>{html.escape(chat_title)}</code>
👤 <b>发送载体</b> : @{html.escape(sender_name)}
⏱ <b>捕获时间</b> : <code>{now_str}</code>

<b>[ 现场原始快照 ]</b>
<blockquote expandable>{preview}</blockquote>{link_line}"""
                    try:
                        await client.send_message(task["alert_channel"], alert_text, link_preview=False)
                        stats.total_hits += 1
                        stats.last_hit_folder = task["folder_name"]
                        stats.last_hit_time = datetime.now()
                    except: pass
                    break
        except Exception as e:
            logger.error("❌ 消息解析流转引擎抛出异常: %s", e, exc_info=True)

async def main():
    config = load_config()
    api_id, api_hash, global_alert, notify_channel, cmd_prefix = validate_config(config)
    folder_rules, system_cache = config.get("folder_rules", {}), config.get("_system_cache", {})
    target_map, valid_rules_count = build_target_map(folder_rules, system_cache, global_alert)
    stats = RadarStats()
    async with TelegramClient(SESSION_NAME, api_id, api_hash) as client:
        client.parse_mode = 'html'
        register_handlers(client, target_map, valid_rules_count, stats, folder_rules, system_cache, notify_channel, global_alert, cmd_prefix)
        silent_flag = os.path.join(WORK_DIR, ".silent_start")
        if os.path.exists(silent_flag):
            try: os.remove(silent_flag)
            except: pass
        else: await send_startup_notification(client, notify_channel, target_map, valid_rules_count, folder_rules, system_cache, cmd_prefix)
        await client.run_until_disconnected()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
