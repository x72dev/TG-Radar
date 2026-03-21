#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

APP_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SRC_DIR="$APP_DIR/src"
VENV_PY="$APP_DIR/venv/bin/python3"
PY="$(command -v python3)"
[ -x "$VENV_PY" ] && PY="$VENV_PY"
PIP="$APP_DIR/venv/bin/pip"
export PYTHONPATH="$SRC_DIR"

SERVICE_PREFIX="$(PYTHONPATH="$SRC_DIR" "$PY" - <<PY
from pathlib import Path
from tgr.config import read_config_data
print(read_config_data(Path(r"$APP_DIR")).get('service_name_prefix') or 'tg-radar')
PY
)"
ADMIN_SVC="${SERVICE_PREFIX}-admin"
CORE_SVC="${SERVICE_PREFIX}-core"
ADMIN_SVC_FILE="/etc/systemd/system/${ADMIN_SVC}.service"
CORE_SVC_FILE="/etc/systemd/system/${CORE_SVC}.service"
PLUGINS_REPO_DIR="$APP_DIR/plugins-external/TG-Radar-Plugins"

C0='\033[0m'; B='\033[1m'; DIM='\033[2m'; CY='\033[36m'; GR='\033[32m'; YE='\033[33m'; RD='\033[31m'
line(){ printf "%b\n" "${DIM}────────────────────────────────────────────────────────${C0}"; }
ok(){ printf "%b\n" "${GR}✔${C0} $*"; }
warn(){ printf "%b\n" "${YE}⚠${C0} $*"; }
err(){ printf "%b\n" "${RD}✖${C0} $*"; }
info(){ printf "%b\n" "${CY}▶${C0} $*"; }
ensure_root(){ [ "$(id -u)" -eq 0 ] || { err "请使用 root 运行 TR。"; exit 1; }; }

create_services() {
  ensure_root
  [ -x "$VENV_PY" ] || { err "缺少虚拟环境 Python：$VENV_PY"; exit 1; }
  cat >"$ADMIN_SVC_FILE" <<SERVICE
[Unit]
Description=TR Manager Admin Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$VENV_PY $APP_DIR/src/radar_admin.py
Restart=always
RestartSec=5
TimeoutStopSec=180
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$SRC_DIR

[Install]
WantedBy=multi-user.target
SERVICE
  cat >"$CORE_SVC_FILE" <<SERVICE
[Unit]
Description=TR Manager Core Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$VENV_PY $APP_DIR/src/radar_core.py
Restart=always
RestartSec=5
TimeoutStopSec=180
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$SRC_DIR

[Install]
WantedBy=multi-user.target
SERVICE
  systemctl daemon-reload
  systemctl enable "$ADMIN_SVC" "$CORE_SVC" >/dev/null 2>&1 || true
  ok "systemd 服务已写入并启用：$ADMIN_SVC / $CORE_SVC"
}

status_view() {
  line
  printf "%b\n" "${B}TR 管理器 · 终端控制台${C0}"
  printf "%b\n" "${DIM}统一管理 Admin / Core 双服务、插件仓库与运行环境。${C0}"
  line
  printf "项目目录 : %s\n" "$APP_DIR"
  printf "源码目录 : %s\n" "$SRC_DIR"
  printf "插件目录 : %s\n" "$PLUGINS_REPO_DIR"
  printf "命令入口 : %s\n" "/usr/local/bin/TR"
  printf "Admin 服务: %s\n" "$ADMIN_SVC"
  printf "Core 服务 : %s\n\n" "$CORE_SVC"
  systemctl status "$ADMIN_SVC" --no-pager -l || true
  printf "\n"
  systemctl status "$CORE_SVC" --no-pager -l || true
}

start_services(){ ensure_root; systemctl start "$ADMIN_SVC" "$CORE_SVC"; ok "TR 管理器双服务已启动。"; }
stop_services(){ ensure_root; systemctl stop "$ADMIN_SVC" "$CORE_SVC" || true; ok "TR 管理器双服务已停止。"; }
restart_services(){ ensure_root; systemctl restart "$ADMIN_SVC" "$CORE_SVC"; ok "TR 管理器双服务已重启。"; }
sync_once(){ cd "$APP_DIR"; PYTHONPATH="$SRC_DIR" "$PY" "$SRC_DIR/sync_once.py"; }
reauth(){ cd "$APP_DIR"; PYTHONPATH="$SRC_DIR" "$PY" "$SRC_DIR/bootstrap_session.py" </dev/tty; }

show_logs() {
  local target="${1:-all}"
  case "$target" in
    admin) journalctl -u "$ADMIN_SVC" -n 100 --no-pager ;;
    core) journalctl -u "$CORE_SVC" -n 100 --no-pager ;;
    all)
      journalctl -u "$ADMIN_SVC" -n 60 --no-pager
      printf "\n"
      journalctl -u "$CORE_SVC" -n 60 --no-pager ;;
    *) err "未知日志目标：$target"; exit 1 ;;
  esac
}

doctor() {
  line
  printf "%b\n" "${B}TR 管理器 · 自检中心${C0}"
  line
  [ -x "$VENV_PY" ] && ok "Python venv: $VENV_PY" || err "缺少 venv Python"
  [ -d "$SRC_DIR/tgr" ] && ok "源码目录: $SRC_DIR" || err "缺少 src/tgr"
  [ -f "$APP_DIR/config.json" ] && ok "配置文件: $APP_DIR/config.json" || err "缺少 config.json"
  [ -d "$PLUGINS_REPO_DIR/plugins" ] && ok "插件目录: $PLUGINS_REPO_DIR/plugins" || warn "插件目录不存在"
  [ -f "$APP_DIR/runtime/radar.db" ] && ok "运行数据库: $APP_DIR/runtime/radar.db" || warn "数据库尚未生成"
  [ -f "$APP_DIR/runtime/sessions/tg_radar_admin.session" ] && ok "Admin session 已存在" || warn "缺少 admin session"
  [ -f "$APP_DIR/runtime/sessions/tg_radar_core.session" ] && ok "Core session 已存在" || warn "缺少 core session"
  systemctl is-enabled "$ADMIN_SVC" >/dev/null 2>&1 && ok "$ADMIN_SVC 已启用" || warn "$ADMIN_SVC 未启用"
  systemctl is-enabled "$CORE_SVC" >/dev/null 2>&1 && ok "$CORE_SVC 已启用" || warn "$CORE_SVC 未启用"
  systemctl is-active "$ADMIN_SVC" >/dev/null 2>&1 && ok "$ADMIN_SVC 运行中" || warn "$ADMIN_SVC 未运行"
  systemctl is-active "$CORE_SVC" >/dev/null 2>&1 && ok "$CORE_SVC 运行中" || warn "$CORE_SVC 未运行"
  [ -x /usr/local/bin/TR ] && ok "TR 命令已存在" || warn "TR 命令不存在"
}

update_repo() {
  ensure_root
  if [ ! -d "$APP_DIR/.git" ]; then err "当前目录不是 Git 仓库，无法执行 TR update。"; exit 1; fi
  info "更新核心仓库"
  git -C "$APP_DIR" pull --ff-only
  if [ -d "$PLUGINS_REPO_DIR/.git" ]; then
    info "更新插件仓库"
    git -C "$PLUGINS_REPO_DIR" pull --ff-only
  else
    warn "未检测到插件仓库 .git，跳过插件更新。"
  fi
  if [ -f "$APP_DIR/requirements.txt" ]; then "$PIP" install -r "$APP_DIR/requirements.txt" >/dev/null; fi
  if [ -f "$PLUGINS_REPO_DIR/requirements.txt" ]; then "$PIP" install -r "$PLUGINS_REPO_DIR/requirements.txt" >/dev/null || true; fi
  create_services
  restart_services
  ok "核心仓库与插件仓库更新完成。"
}

menu() {
  clear
  line
  printf "%b\n" "${B}TR 管理器 · Terminal Radar${C0}"
  printf "%b\n" "${DIM}第一版功能基线 + 插件全解耦 + 一键部署。${C0}"
  line
  cat <<'MENU'
1) 写入 / 刷新 systemd 双服务
2) 启动双服务
3) 停止双服务
4) 重启双服务
5) 查看服务状态
6) 查看日志（admin）
7) 查看日志（core）
8) 执行一次同步
9) 重新执行 Telegram 授权
10) 运行环境自检
11) 更新核心仓库与插件仓库
0) 退出
MENU
  line
}

case "${1:-menu}" in
  install-services) create_services ;;
  start) start_services ;;
  stop) stop_services ;;
  restart) restart_services ;;
  status) status_view ;;
  sync) sync_once ;;
  reauth) reauth ;;
  doctor) doctor ;;
  update) update_repo ;;
  logs) show_logs "${2:-all}" ;;
  menu)
    while true; do
      menu
      if [ -r /dev/tty ]; then read -rp "请选择: " choice </dev/tty; else read -rp "请选择: " choice; fi
      case "$choice" in
        1) create_services ;;
        2) start_services ;;
        3) stop_services ;;
        4) restart_services ;;
        5) status_view ;;
        6) show_logs admin ;;
        7) show_logs core ;;
        8) sync_once ;;
        9) reauth ;;
        10) doctor ;;
        11) update_repo ;;
        0) exit 0 ;;
        *) warn "无效选项，请重新输入菜单编号。" ;;
      esac
      echo
      if [ -r /dev/tty ]; then read -rp "按回车返回菜单..." _dummy </dev/tty; else read -rp "按回车返回菜单..." _dummy; fi
    done ;;
  *)
    cat <<USAGE
用法:
  TR                      打开交互菜单
  TR status               查看服务状态
  TR start|stop|restart   管理双服务
  TR sync                 执行一次同步
  TR reauth               重新执行 Telegram 授权
  TR logs [admin|core]    查看日志
  TR doctor               运行环境自检
  TR update               更新核心仓库与插件仓库并重启
USAGE
    exit 1 ;;
esac
