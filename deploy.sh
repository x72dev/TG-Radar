#!/bin/bash
# ============================================================
#  TG-Radar  v5.1.1  —  Management Script
#  Path : /root/TG-Radar
#  Cmd  : TGR
# ============================================================
set -uo pipefail

# ── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

_i() { echo -e "${CYAN} ➜  ${RESET}$*"; }
_ok(){ echo -e "${GREEN} ✔  ${RESET}$*"; }
_w() { echo -e "${YELLOW} ⚠  ${RESET}$*"; }
_e() { echo -e "${RED} ✖  ${RESET}$*"; }
_bar(){ echo -e "  ${DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"; }
_pause(){ echo ""; read -rp "  按回车键返回控制台 ..." _DUMMY; }

# ── Constants ────────────────────────────────────────────────
APP_DIR="/root/TG-Radar"
SVC="tg_monitor"
SVC_FILE="/etc/systemd/system/${SVC}.service"
SYNC_BIN="$APP_DIR/sync_engine.py"
MON_BIN="$APP_DIR/tg_monitor.py"
PY="$APP_DIR/venv/bin/python3"
TGR_CMD="/usr/local/bin/TGR"
REPO="chenmo8848/TG-Radar"
VER="v5.1.1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Helpers ──────────────────────────────────────────────────
_svc_active()  { systemctl is-active  --quiet "$SVC" 2>/dev/null; }
_svc_enabled() { systemctl is-enabled --quiet "$SVC" 2>/dev/null; }
_venv_ok()     { [ -f "$PY" ]; }
_cfg_ok()      { [ -f "$APP_DIR/config.json" ]; }
_api_ok() {
    _cfg_ok || return 1
    local id
    id=$(python3 -c "import json; print(json.load(open('$APP_DIR/config.json')).get('api_id',''))" 2>/dev/null || true)
    [ -n "$id" ] && [ "$id" != "1234567" ]
}

_try_start() {
    sudo systemctl start "$SVC" 2>/dev/null && sleep 1 || true
    _svc_active && _ok "监控服务已启动" || _e "启动失败  →  journalctl -u $SVC -n 20"
}

# ============================================================
#  Startup: silent update check
# ============================================================
_startup_update_check() {
    local json latest dl_url
    json=$(curl -fsSL --connect-timeout 5 "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null) || return 0

    latest=$(echo "$json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tag_name',''))" 2>/dev/null) || return 0
    clean_latest=$(echo "$latest" | sed 's/^[vV]//')
    clean_ver=$(echo "$VER" | sed 's/^[vV]//')
    
    if [ -z "$clean_latest" ] || [ "$clean_latest" = "$clean_ver" ]; then return 0; fi
    
    # 【终极修复】使用 Python 进行绝对精准的版本号元组比对，杜绝版本倒退 Bug
    is_newer=$(python3 -c "
def v(s): return tuple(map(int, (s.strip().split('.') + ['0','0','0'])[:3]))
try:
    print('YES' if v('$clean_latest') > v('$clean_ver') else 'NO')
except:
    print('NO')
" 2>/dev/null || echo "NO")

    if [ "$is_newer" != "YES" ]; then
        return 0
    fi

    dl_url=$(echo "$json" | python3 -c "
import sys,json
a=[x['browser_download_url'] for x in json.load(sys.stdin).get('assets',[]) if x['name'].endswith('.zip')]
print(a[0] if a else '')" 2>/dev/null) || return 0
    [ -z "$dl_url" ] && return 0

    clear
    echo ""
    echo -e "${BOLD}  ╔══════════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}  ║               发现新版本                             ║${RESET}"
    echo -e "${BOLD}  ╚══════════════════════════════════════════════════════╝${RESET}"
    echo ""
    echo -e "  当前版本  ${DIM}${VER}${RESET}"
    echo -e "  最新版本  ${GREEN}${BOLD}${latest}${RESET}"
    echo ""
    _bar
    echo ""
    echo -e "  ${BOLD}${GREEN}1${RESET}  快速更新      保留全部配置，更新文件后自动重启服务"
    echo -e "  ${BOLD}${CYAN}2${RESET}  完整重新部署  更新文件后重走完整向导，可重新配置"
    echo -e "  ${BOLD}3${RESET}  跳过          本次不更新"
    echo ""
    read -rp "  请选择 [1/2/3，回车=跳过] ：" _upd
    _upd="${_upd:-3}"
    echo ""

    case "$_upd" in
        1|2)
            _i "正在下载 ${latest} ..."
            if curl -fsSL "$dl_url" -o /tmp/TG_Radar_update.zip 2>/dev/null; then
                [ -f "$APP_DIR/config.json" ] && cp "$APP_DIR/config.json" /tmp/_tgr_cfg.bak
                unzip -q -o /tmp/TG_Radar_update.zip -d "$APP_DIR" 2>/dev/null
                rm -f /tmp/TG_Radar_update.zip
                [ -f /tmp/_tgr_cfg.bak ] && cp /tmp/_tgr_cfg.bak "$APP_DIR/config.json" && rm -f /tmp/_tgr_cfg.bak
                chmod +x "$APP_DIR/deploy.sh" 2>/dev/null || true
                _ok "已更新至 ${latest}"
                echo ""
                if [ "$_upd" = "1" ]; then
                    _i "重启监控服务..."
                    sudo systemctl restart "$SVC" 2>/dev/null && sleep 1 || true
                    _svc_active && _ok "服务已重启，最新版本运行中。" || _w "重启失败  →  journalctl -u $SVC -n 20"
                    echo ""
                    echo -e "  ${GREEN}${BOLD}快速更新完成！所有配置已保留。${RESET}"
                    echo ""
                    read -rp "  按回车进入管理菜单 ..." _DUMMY
                    exec bash "$APP_DIR/deploy.sh"
                else
                    echo -e "  ${GREEN}3 秒后以新版本重新启动...${RESET}"
                    sleep 3
                    exec bash "$APP_DIR/deploy.sh"
                fi
            else
                _w "下载失败，继续使用本地版本。"
                echo ""
                read -rp "  按回车继续 ..." _DUMMY
            fi
            ;;
        *)
            _i "已跳过更新。"
            sleep 1
            ;;
    esac
}

_startup_update_check

# ============================================================
#  Main menu
# ============================================================
_menu() {
    clear
    echo ""
    echo -e "${CYAN}${BOLD}  ▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰${RESET}"
    echo -e "${BOLD}         TG-Radar 态势感知引擎 · 核心部署管家${RESET}"
    echo -e "${DIM}                      v5.1.1                          ${RESET}"
    echo -e "${CYAN}${BOLD}  ▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰${RESET}"
    echo ""

    if _svc_active; then printf "  ${GREEN}●${RESET}  服务    ${GREEN}运行中${RESET}\n"
    elif _svc_enabled; then printf "  ${YELLOW}○${RESET}  服务    ${YELLOW}已停止${RESET}  ${DIM}(开机自启已注册)${RESET}\n"
    else printf "  ${RED}✕${RESET}  服务    ${RED}未安装${RESET}\n"; fi

    if _api_ok; then echo -e "      ${GREEN}●${RESET}  配置    ${GREEN}就绪${RESET}"
    elif _cfg_ok; then echo -e "      ${YELLOW}○${RESET}  配置    ${YELLOW}待填写${RESET}"
    else echo -e "      ${RED}✕${RESET}  配置    ${RED}不存在${RESET}"; fi

    [ -f "$TGR_CMD" ] && echo -e "  ${GREEN}●${RESET}  TGR     ${GREEN}已注册${RESET}" || echo -e "  ${YELLOW}○${RESET}  TGR     ${YELLOW}未注册${RESET}"

    _bar; echo ""
    echo -e "  ${BOLD}${GREEN} 1 ${RESET}  一键部署  ${DIM}全程引导：环境 + 配置 + 授权${RESET}"
    echo -e "  ${BOLD}${CYAN} 2 ${RESET}  停止服务"
    echo -e "  ${BOLD}${CYAN} 3 ${RESET}  启动服务"
    echo -e "  ${BOLD}${CYAN} 4 ${RESET}  重启服务"
    echo -e "  ${BOLD}${CYAN} 5 ${RESET}  状态与日志"
    echo -e "  ${BOLD}${YELLOW} 6 ${RESET}  重新授权  ${DIM}session 失效 / 切换账号${RESET}"
    echo -e "  ${BOLD}${RED} 7 ${RESET}  完全卸载"
    echo -e "  ${BOLD} 0 ${RESET}  退出\n"
}

_deploy() {
    clear; echo ""; echo -e "${BOLD}  ╔══════════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}  ║                   一键部署向导                       ║${RESET}"
    echo -e "${BOLD}  ╚══════════════════════════════════════════════════════╝${RESET}"
    echo ""; echo -e "  ${DIM}阶段一  系统环境  ·  阶段二  填写配置  ·  阶段三  授权启动${RESET}"; echo ""

    read -rp "  自动同步间隔（分钟，回车=30，1440=每天）：" _mins
    _mins="${_mins:-30}"
    if ! [[ "$_mins" =~ ^[0-9]+$ ]] || [ "$_mins" -lt 1 ]; then _w "无效，使用默认值 30 分钟。"; _mins=30; fi
    if   [ "$_mins" -ge 1440 ]; then _DAYS=$(( _mins / 1440 )); _CRON="0 0 */$_DAYS * *"; echo -e "  同步周期  ${BOLD}每 ${_DAYS} 天${RESET}"
    elif [ "$_mins" -ge 60 ]; then _HRS=$(( _mins / 60 )); _CRON="0 */$_HRS * * *"; echo -e "  同步周期  ${BOLD}每 ${_HRS} 小时${RESET}"
    else _CRON="*/$_mins * * * *"; echo -e "  同步周期  ${BOLD}每 ${_mins} 分钟${RESET}"; fi
    echo ""; read -rp "  按回车开始，Ctrl+C 取消 ：" _DUMMY; echo ""

    _bar; echo -e "  ${BOLD}阶段一  系统环境${RESET}"; _bar; echo ""
    declare -a _RES=(); _PASS=0; _TOTAL=5

    _step() {
        local n="$1" label="$2"; shift 2
        echo -ne "  [${n}/${_TOTAL}]  ${label} ..."
        if "$@" > /tmp/tgr_deploy.log 2>&1; then
            echo -e "  ${GREEN}完成${RESET}"; _PASS=$((_PASS+1)); _RES+=("${GREEN}  ✓${RESET}  ${label}")
        else
            echo -e "  ${RED}失败${RESET}"; _RES+=("${RED}  ✗${RESET}  ${label}  ${DIM}(cat /tmp/tgr_deploy.log)${RESET}")
        fi
    }

    _step 1 "安装系统依赖" bash -c "apt-get update -y >/dev/null && apt-get install -y python3 python3-venv python3-pip cron >/dev/null"
    _step 2 "同步项目文件" bash -c "mkdir -p '$APP_DIR'; chmod +x '$APP_DIR/deploy.sh'"
    _step 3 "配置 Python 虚拟环境" bash -c "cd '$APP_DIR'; [ ! -d venv ] && python3 -m venv venv; ./venv/bin/pip install --upgrade pip >/dev/null; ./venv/bin/pip install telethon requests >/dev/null"
    
    _step 4 "注册 systemd 服务" bash -c "
        printf '[Unit]\nDescription=TG-Radar v5.1.1\nAfter=network.target\n\n[Service]\nType=simple\nUser=root\nWorkingDirectory=$APP_DIR\nExecStart=$PY $MON_BIN\nRestart=always\nRestartSec=5\nStandardOutput=journal\nStandardError=journal\n\n[Install]\nWantedBy=multi-user.target\n' > '$SVC_FILE'
        systemctl daemon-reload && systemctl enable '$SVC' >/dev/null 2>&1
    "

    # 【终极修复】采用 mktemp 接管 cron 注入，免疫管线截断
    _step 5 "写入 Cron & 注册 TGR" bash -c "
        tmp_cron=\$(mktemp)
        crontab -l > \"\$tmp_cron\" 2>/dev/null || true
        sed -i '/sync_engine\.py/d' \"\$tmp_cron\" 2>/dev/null || true
        sed -i '/journalctl.*vacuum/d' \"\$tmp_cron\" 2>/dev/null || true
        echo '$_CRON $PY $SYNC_BIN > /dev/null 2>&1' >> \"\$tmp_cron\"
        echo '0 3 * * * journalctl --vacuum-time=1d >/dev/null 2>&1' >> \"\$tmp_cron\"
        crontab \"\$tmp_cron\"
        rm -f \"\$tmp_cron\"
        printf '#!/bin/bash\nexec bash /root/TG-Radar/deploy.sh \"\$@\"\n' > '$TGR_CMD'
        chmod +x '$TGR_CMD'
    "

    echo ""; _bar; echo -e "  阶段一结果  ${_PASS}/${_TOTAL} 完成"; _bar
    for r in "${_RES[@]}"; do echo -e "$r"; done
    _bar
    if [ "$_PASS" -lt "$_TOTAL" ]; then echo ""; _e "环境部署失败，无法继续。"; _pause; return; fi
    echo ""; echo -e "  ${GREEN}环境就绪，3 秒后进入配置...${RESET}"; sleep 3

    clear; echo ""; _bar; echo -e "  ${BOLD}阶段二  填写配置${RESET}"; _bar; echo ""
    if _api_ok; then
        local _cid
        _cid=$(python3 -c "import json; print(json.load(open('$APP_DIR/config.json')).get('api_id',''))" 2>/dev/null)
        echo -e "  检测到现有配置  ${DIM}api_id = ${_cid}${RESET}\n"
        read -rp "  保留现有配置跳过此步骤？[Y/n] ：" _skip
        _skip="${_skip:-Y}"
        if [ "$_skip" = "Y" ] || [ "$_skip" = "y" ]; then _ok "使用现有配置。"; sleep 1; else _fill_config; fi
    else _fill_config; fi

    clear; echo ""; _bar; echo -e "  ${BOLD}阶段三  账号授权${RESET}"; _bar; echo ""
    echo -e "  ${DIM}sync_engine.py 将登录账号、拉取分组、启动守护进程${RESET}\n"
    read -rp "  按回车开始，Ctrl+C 取消 ：" _DUMMY; echo ""

    cd "$APP_DIR"; "$PY" "$SYNC_BIN"; local _exit=$?

    echo ""; _bar; echo -e "  ${BOLD}最终验证${RESET}"; _bar; echo ""
    local _ok=true
    [ -f "$SVC_FILE" ]  && _ok "系统服务已注册"      || { _e "服务注册失败";      _ok=false; }
    [ -f "$PY" ]        && _ok "Python 环境就绪"     || { _e "Python 环境异常";   _ok=false; }
    [ -f "$TGR_CMD" ]   && _ok "TGR 命令已注册"      || { _e "TGR 注册失败";      _ok=false; }
    _api_ok             && _ok "config.json 配置完毕" || { _e "config.json 未配置"; _ok=false; }

    if [ "$_exit" -eq 0 ]; then
        _ok "Telegram 授权成功，分组已同步"; sleep 1
        if _svc_active; then _ok "监控服务运行中"
        else _w "服务未自动启动，尝试手动启动..."; _try_start; fi
    else
        _e "Telegram 授权失败（退出码 $_exit）"
        echo -e "  ${DIM}可能原因：api_id/api_hash 有误、验证码超时、网络异常${RESET}"; _ok=false
    fi

    echo ""; _bar
    if [ "$_ok" = true ]; then
        local _pfx
        _pfx=$(python3 -c "import json; print(json.load(open('$APP_DIR/config.json')).get('cmd_prefix','-'))" 2>/dev/null || echo "-")
        echo ""; echo -e "  ${GREEN}${BOLD}全部完成！雷达已上线。${RESET}\n"
        echo -e "  ${BOLD}在告警频道或 Saved Messages 发送指令：${RESET}"
        echo -e "  ${CYAN}${_pfx}folders${RESET}  ${CYAN}${_pfx}enable <分组>${RESET}  ${CYAN}${_pfx}help${RESET}\n"
    else
        echo ""; _w "部分步骤未完成，请检查后重试选项 1。"
        echo -e "  ${DIM}journalctl -u $SVC -n 30${RESET}\n"
    fi
    _pause
}

_fill_config() {
    echo -e "  ${YELLOW}前往 https://my.telegram.org → API development tools 获取凭证${RESET}\n"
    local _id _hash
    while true; do read -rp "  api_id（纯数字）：" _id; [[ "$_id" =~ ^[0-9]+$ ]] && [ "$_id" != "1234567" ] && break; _w "无效的 api_id。"; done
    while true; do read -rp "  api_hash（至少 16 位）：" _hash; [ ${#_hash} -ge 16 ] && break; _w "api_hash 长度不足。"; done

    python3 - << PYEOF2
import json, os
path = '$APP_DIR/config.json'
cfg  = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}
cfg.update({'api_id': int('$_id'), 'api_hash': '$_hash'})
for k,v in [('folder_rules',{}),('_system_cache',{}),('global_alert_channel_id',None),('notify_channel_id',None),('cmd_prefix','-')]:
    cfg.setdefault(k,v)
tmp = path+'.tmp'; json.dump(cfg,open(tmp,'w',encoding='utf-8'),indent=4,ensure_ascii=False); os.replace(tmp,path)
PYEOF2

    echo ""; _i "连接 Telegram，拉取分组和频道列表..."; echo -e "  ${YELLOW}首次登录需要输入手机号和验证码${RESET}\n"
    local _fetch
    _fetch=$("$PY" - << 'PYEOF3'
import asyncio, json
from telethon import TelegramClient, functions, types, utils
APP = '/root/TG-Radar'
async def run():
    cfg = json.load(open(f'{APP}/config.json', encoding='utf-8'))
    c   = TelegramClient(f'{APP}/TG_Radar_session', cfg['api_id'], cfg['api_hash'])
    await c.start()
    res = await c(functions.messages.GetDialogFiltersRequest())
    fds = [f for f in getattr(res,'filters',[]) if isinstance(f, types.DialogFilter)]
    folders=[]
    for f in fds:
        t = f.title.text if hasattr(f.title,'text') else str(f.title)
        ids=set()
        for peer in f.include_peers:
            try:
                pid = utils.get_peer_id(peer)
                t_name = type(peer).__name__
                if 'Channel' in t_name: ids.add(int(f"-100{pid}"))
                elif 'Chat' in t_name: ids.add(int(f"-{pid}"))
                else: ids.add(pid)
            except: pass
        if getattr(f,'groups',False) or getattr(f,'broadcasts',False):
            async for d in c.iter_dialogs():
                if f.groups and d.is_group: ids.add(d.id)
                elif f.broadcasts and d.is_channel and not d.is_group: ids.add(d.id)
        folders.append({'id':f.id,'title':t,'group_ids':list(ids)})
    channels=[]
    async for d in c.iter_dialogs():
        if d.is_channel and not d.is_group: channels.append({'id':d.id,'name':d.name})
    await c.disconnect()
    print('__JSON__'+json.dumps({'folders':folders,'channels':channels},ensure_ascii=False))
asyncio.run(run())
PYEOF3
)
    local _json; _json=$(echo "$_fetch" | grep '__JSON__' | sed 's/__JSON__//')
    if [ -z "$_json" ]; then _e "获取数据失败，请检查 api_id / api_hash。"; return 1; fi
    _ok "数据拉取成功！\n"; echo -e "  ${BOLD}选择要监控的分组${RESET}  ${DIM}（多个编号空格分隔，回车=全选）${RESET}\n"

    local _fcnt; _fcnt=$(echo "$_json" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['folders']))")
    echo "$_json" | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'  {i})  {f[\"title\"]}  ({len(f[\"group_ids\"])} 个群组)') for i,f in enumerate(d['folders'],1)]"
    echo ""
    if [ "$_fcnt" -eq 0 ]; then _w "未检测到自定义分组。"; return 1; fi

    local _sel
    while true; do
        read -rp "  输入编号：" _sel
        [ -z "$_sel" ] && _sel=$(seq 1 "$_fcnt" | tr '\n' ' ')
        local _ok_sel=true
        for n in $_sel; do if ! [[ "$n" =~ ^[0-9]+$ ]] || [ "$n" -lt 1 ] || [ "$n" -gt "$_fcnt" ]; then _ok_sel=false; break; fi; done
        [ "$_ok_sel" = true ] && break
        _w "编号无效，请重新输入。"
    done

    echo ""; echo "$_json" | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'  + {d[\"folders\"][int(i)-1][\"title\"]}') for i in '$_sel'.split()]"
    echo ""; echo -e "  ${BOLD}选择告警频道${RESET}\n"
    
    local _chnc; _chnc=$(echo "$_json" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['channels']))")
    echo "$_json" | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'  {i})  {c[\"name\"]}  ({c[\"id\"]})') for i,c in enumerate(d['channels'],1)]"
    echo ""

    local _alert_ch
    if [ "$_chnc" -eq 0 ]; then
        _w "未找到频道，请手动输入频道 ID。"
        while true; do read -rp "  告警频道 ID：" _alert_ch; [[ "$_alert_ch" =~ ^-?[0-9]+$ ]] && break; done
    else
        while true; do
            read -rp "  输入编号 [1-${_chnc}] 或直接输入频道 ID ：" _s
            if [[ "$_s" =~ ^[0-9]+$ ]] && [ "$_s" -ge 1 ] && [ "$_s" -le "$_chnc" ]; then
                _alert_ch=$(echo "$_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['channels'][$_s-1]['id'])")
                break
            elif [[ "$_s" =~ ^-?[0-9]+$ ]]; then _alert_ch="$_s"; break; fi
        done
    fi

    echo ""; echo -e "  ${BOLD}通知频道${RESET}  ${DIM}（直接回车 = 与告警频道相同）${RESET}\n"
    local _notify_ch _notify_val
    read -rp "  通知频道 ID：" _notify_ch
    [[ "${_notify_ch:-}" =~ ^-?[0-9]+$ ]] && _notify_val="$_notify_ch" || _notify_val="null"

    echo ""; local _pfx; read -rp "  ChatOps 指令前缀（回车默认 -）：" _pfx; _pfx="${_pfx:--}"
    _i "写入 config.json ..."
    echo "$_json" > /tmp/_tgr_data.json
    python3 - << PYEOF7
import json, os
data = json.load(open('/tmp/_tgr_data.json', encoding='utf-8'))
sel  = [int(x)-1 for x in '$_sel'.split()]
path = '$APP_DIR/config.json'
cfg  = json.load(open(path, encoding='utf-8'))
cfg['api_id']                  = int('$_id')
cfg['api_hash']                = '$_hash'
cfg['global_alert_channel_id'] = int('$_alert_ch')
cfg['notify_channel_id']       = $_notify_val if '$_notify_val' != 'null' else None
cfg['cmd_prefix']              = '$_pfx'
fr={}; sc={}
for i in sel:
    f=data['folders'][i]; t=f['title']
    fr[t]={'id':f['id'],'enable':True,'alert_channel_id':None,'rules':{f'🟢 {t}监控':'(示范词A|示范词B)'}}
    sc[t]=f['group_ids']
cfg['folder_rules']=fr; cfg['_system_cache']=sc
tmp=path+'.tmp'; json.dump(cfg,open(tmp,'w',encoding='utf-8'),indent=4,ensure_ascii=False); os.replace(tmp,path)
os.remove('/tmp/_tgr_data.json')
PYEOF7

    echo -e "\n  ${GREEN}配置已写入${RESET}\n  3 秒后进入授权步骤..."; sleep 3
}

_stop() { clear; echo -e "\n  ${BOLD}停止服务${RESET}\n"; if ! _svc_active; then _w "未运行。"; _pause; return; fi; sudo systemctl stop "$SVC" 2>/dev/null && _ok "已停止。" || _e "停止失败"; _pause; }
_start() { clear; echo -e "\n  ${BOLD}启动服务${RESET}\n"; [ ! -f "$SVC_FILE" ] && { _e "未安装。"; _pause; return; }; if _svc_active; then _w "已运行。"; _pause; return; fi; sudo systemctl start "$SVC" 2>/dev/null && sleep 1 || true; if _svc_active; then _ok "已启动。"; else _e "失败"; fi; _pause; }
_restart() { clear; echo -e "\n  ${BOLD}重启服务${RESET}\n"; [ ! -f "$SVC_FILE" ] && { _e "未安装。"; _pause; return; }; sudo systemctl restart "$SVC" 2>/dev/null && sleep 1 || true; if _svc_active; then _ok "已重启。"; else _e "失败"; fi; _pause; }
_status() { clear; echo -e "\n  ${BOLD}系统状态${RESET}\n"; _svc_active && echo -e "  ${GREEN}●${RESET} 服务运行中" || echo -e "  ${RED}○${RESET} 服务未运行"; journalctl -u "$SVC" -n 20 --no-pager 2>/dev/null || true; _pause; }
_reauth() { clear; echo -e "\n  ${BOLD}重新授权${RESET}\n"; cd "$APP_DIR"; "$PY" "$SYNC_BIN"; [ $? -eq 0 ] && _ok "授权成功" || _e "失败"; _pause; }
_uninstall() { clear; echo -e "\n  ${BOLD}${RED}卸载服务${RESET}\n"; read -rp "  确认删除? (yes): " c; [ "$c" != "yes" ] && return; systemctl stop "$SVC" 2>/dev/null; systemctl disable "$SVC" 2>/dev/null; rm -f "$SVC_FILE"; rm -f "$TGR_CMD"; crontab -l | grep -v 'sync_engine' | crontab -; _ok "已卸载"; _pause; }

while true; do
    _menu; read -rp "  请输入选项 [0-7] ：" _choice; echo ""
    case "$_choice" in 1) _deploy;; 2) _stop;; 3) _start;; 4) _restart;; 5) _status;; 6) _reauth;; 7) _uninstall;; 0) echo -e "  ${GREEN}已退出。${RESET}\n"; exit 0;; *) _w "无效选项。"; sleep 1;; esac
done
