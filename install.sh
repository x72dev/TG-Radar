#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

REPO_URL_DEFAULT="https://github.com/chenmo8848/TG-Radar.git"
PLUGINS_REPO_URL_DEFAULT="https://github.com/chenmo8848/TG-Radar-Plugins.git"
INSTALL_DIR_DEFAULT="/root/TG-Radar"
BRANCH_DEFAULT="main"
PLUGINS_BRANCH_DEFAULT="main"
PYTHON_BIN_DEFAULT="python3"

REPO_URL="${TR_REPO_URL:-${TGRC_REPO_URL:-$REPO_URL_DEFAULT}}"
PLUGINS_REPO_URL="${TR_PLUGINS_REPO_URL:-$PLUGINS_REPO_URL_DEFAULT}"
INSTALL_DIR="${TR_INSTALL_DIR:-$INSTALL_DIR_DEFAULT}"
BRANCH="${TR_BRANCH:-$BRANCH_DEFAULT}"
PLUGINS_BRANCH="${TR_PLUGINS_BRANCH:-$PLUGINS_BRANCH_DEFAULT}"
PYTHON_BIN="${TR_PYTHON_BIN:-$PYTHON_BIN_DEFAULT}"

C0='\033[0m'; B='\033[1m'; DIM='\033[2m'; CY='\033[36m'; GR='\033[32m'; YE='\033[33m'; RD='\033[31m'
step(){ printf "%b\n" "${CY}▶${C0} $*"; }
ok(){ printf "%b\n" "${GR}✔${C0} $*"; }
warn(){ printf "%b\n" "${YE}⚠${C0} $*"; }
err(){ printf "%b\n" "${RD}✖${C0} $*"; }
line(){ printf "%b\n" "${DIM}────────────────────────────────────────────────────────${C0}"; }

die(){ err "$*"; exit 1; }
need_root(){ [ "$(id -u)" -eq 0 ] || die "请使用 root 执行安装脚本。"; }
read_tty(){ local __var="$1"; shift; local __prompt="$1"; shift || true; local __value=""; if [ -r /dev/tty ]; then read -r -p "$__prompt" __value </dev/tty || true; else read -r -p "$__prompt" __value || true; fi; printf -v "$__var" '%s' "$__value"; }

clone_or_update(){
  local repo_url="$1"; local repo_dir="$2"; local repo_branch="$3"
  mkdir -p "$(dirname "$repo_dir")"
  if [ -d "$repo_dir/.git" ]; then
    git -C "$repo_dir" fetch --all --tags --prune >/dev/null 2>&1 || true
    git -C "$repo_dir" checkout -f "$repo_branch" >/dev/null 2>&1 || true
    git -C "$repo_dir" reset --hard "origin/$repo_branch" >/dev/null 2>&1 || true
  else
    rm -rf "$repo_dir"
    git clone --depth=1 --branch "$repo_branch" "$repo_url" "$repo_dir" >/dev/null
  fi
}

need_root
export DEBIAN_FRONTEND=noninteractive

SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" 2>/dev/null && pwd -P || pwd -P)"
is_remote_bootstrap=0
case "$SCRIPT_PATH" in
  /dev/fd/*|/proc/self/fd/*|/tmp/*|stdin) is_remote_bootstrap=1 ;;
esac

if [ "$is_remote_bootstrap" -eq 1 ] || [ ! -f "$SCRIPT_DIR/requirements.txt" ] || [ ! -f "$SCRIPT_DIR/config.example.json" ] || [ ! -f "$SCRIPT_DIR/deploy.sh" ] || [ ! -d "$SCRIPT_DIR/src/tgr" ]; then
  line
  printf "%b\n" "${B}TR 管理器 · 一键安装${C0}"
  printf "%b\n" "${DIM}脚本将先拉取核心仓库，再继续完整安装流程。${C0}"
  line
  step "安装系统依赖"
  apt-get update -y >/dev/null
  apt-get install -y git curl ca-certificates python3 python3-venv python3-pip systemd cron >/dev/null
  ok "系统依赖已就绪"
  step "拉取核心仓库"
  clone_or_update "$REPO_URL" "$INSTALL_DIR" "$BRANCH"
  ok "核心仓库已就绪：$INSTALL_DIR"
  exec env TR_INSTALL_DIR="$INSTALL_DIR" TR_REPO_URL="$REPO_URL" TR_BRANCH="$BRANCH" TR_PLUGINS_REPO_URL="$PLUGINS_REPO_URL" TR_PLUGINS_BRANCH="$PLUGINS_BRANCH" bash "$INSTALL_DIR/install.sh"
fi

APP_DIR="$SCRIPT_DIR"
PLUGINS_REPO_DIR="$APP_DIR/plugins-external/TG-Radar-Plugins"
VENV_DIR="$APP_DIR/venv"
PY="$VENV_DIR/bin/python3"
PIP="$VENV_DIR/bin/pip"

line
printf "%b\n" "${B}TR 管理器 · 一键安装${C0}"
printf "%b\n" "${DIM}以第一版功能为基线，插件全解耦，统一文案与部署口径。${C0}"
line

step "安装系统依赖"
apt-get update -y >/dev/null
apt-get install -y git curl ca-certificates python3 python3-venv python3-pip systemd cron >/dev/null
ok "系统依赖已就绪"

step "拉取插件仓库到内嵌目录"
clone_or_update "$PLUGINS_REPO_URL" "$PLUGINS_REPO_DIR" "$PLUGINS_BRANCH"
ok "插件仓库已就绪：$PLUGINS_REPO_DIR"

step "初始化 Python 运行环境"
[ -d "$VENV_DIR" ] || "$PYTHON_BIN" -m venv "$VENV_DIR"
"$PY" -m pip install --upgrade pip wheel setuptools >/dev/null
"$PIP" install -r "$APP_DIR/requirements.txt" >/dev/null
if [ -f "$PLUGINS_REPO_DIR/requirements.txt" ]; then
  "$PIP" install -r "$PLUGINS_REPO_DIR/requirements.txt" >/dev/null || true
fi
mkdir -p "$APP_DIR/runtime/logs" "$APP_DIR/runtime/sessions" "$APP_DIR/runtime/backups"
ok "Python 环境、依赖与 runtime 目录已准备完成"

step "准备配置文件"
[ -f "$APP_DIR/config.json" ] || cp "$APP_DIR/config.example.json" "$APP_DIR/config.json"
current_api_id="$($PY - <<PY
import json, pathlib
p=pathlib.Path(r"$APP_DIR")/'config.json'
try:
 d=json.loads(p.read_text(encoding='utf-8'))
 v=d.get('api_id')
 print('' if v in (None,0,1234567) else v)
except Exception:
 print('')
PY
)"
current_api_hash="$($PY - <<PY
import json, pathlib
p=pathlib.Path(r"$APP_DIR")/'config.json'
try:
 d=json.loads(p.read_text(encoding='utf-8'))
 v=d.get('api_hash') or ''
 print('' if v=='xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' else v)
except Exception:
 print('')
PY
)"
if [ -z "${API_ID:-}" ] && [ -n "$current_api_id" ]; then API_ID="$current_api_id"; fi
if [ -z "${API_HASH:-}" ] && [ -n "$current_api_hash" ]; then API_HASH="$current_api_hash"; fi
[ -n "${API_ID:-}" ] || read_tty API_ID "请输入 Telegram API_ID: "
[ -n "${API_HASH:-}" ] || read_tty API_HASH "请输入 Telegram API_HASH: "
[ -n "${API_ID:-}" ] || die "缺少 API_ID"
[ -n "${API_HASH:-}" ] || die "缺少 API_HASH"
"$PY" - <<PY
import json, pathlib
p=pathlib.Path(r"$APP_DIR")/'config.json'
d=json.loads(p.read_text(encoding='utf-8'))
d['api_id']=int("${API_ID}")
d['api_hash']="${API_HASH}"
d['repo_url']="${REPO_URL}"
d['plugins_repo_url']="${PLUGINS_REPO_URL}"
d['plugins_dir']="./plugins-external/TG-Radar-Plugins/plugins"
p.write_text(json.dumps(d, ensure_ascii=False, indent=4)+'\n', encoding='utf-8')
PY
ok "config.json 已写入关键参数"

step "准备全局命令 TR"
cat >/usr/local/bin/TR <<WRAP
#!/usr/bin/env bash
cd "$APP_DIR"
exec bash "$APP_DIR/deploy.sh" "\$@"
WRAP
chmod +x /usr/local/bin/TR
ok "终端管理器已注册：TR"

step "写入 / 刷新 systemd 服务"
bash "$APP_DIR/deploy.sh" install-services >/dev/null
ok "systemd 双服务已写入"

step "检查 Telegram 会话"
if [ ! -f "$APP_DIR/runtime/sessions/tg_radar_admin.session" ] || [ ! -f "$APP_DIR/runtime/sessions/tg_radar_core.session" ] || [ ! -f "$APP_DIR/runtime/sessions/tg_radar_admin_worker.session" ]; then
  step "开始首次授权"
  PYTHONPATH="$APP_DIR/src" "$PY" "$APP_DIR/src/bootstrap_session.py" </dev/tty
  ok "Telegram 首次授权完成"
else
  ok "检测到现有会话，跳过首次授权"
fi

step "执行首次同步"
PYTHONPATH="$APP_DIR/src" "$PY" "$APP_DIR/src/sync_once.py"
ok "首次同步完成"

step "启动双服务"
systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable --now tg-radar-admin tg-radar-core >/dev/null
ok "Admin / Core 双服务已启动"

line
printf "%b\n" "${B}TR 管理器 · 安装完成${C0}"
printf "项目目录 : %s\n" "$APP_DIR"
printf "插件目录 : %s\n" "$PLUGINS_REPO_DIR"
printf "终端命令 : %s\n" "TR"
printf "Telegram 命令前缀 : %s\n" "-"
line
printf "%b\n" "${DIM}常用命令：TR status / TR doctor / TR update / TR logs admin / TR logs core${C0}"
printf "%b\n" "${DIM}Telegram 收藏夹命令：-help / -status / -folders / -plugins${C0}"
