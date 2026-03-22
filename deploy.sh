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
SVC="${SERVICE_PREFIX}"
SVC_FILE="/etc/systemd/system/${SVC}.service"
PLUGINS_REPO_DIR="$APP_DIR/plugins-external/TG-Radar-Plugins"

C0='\033[0m'; B='\033[1m'; DIM='\033[2m'; CY='\033[36m'; GR='\033[32m'; YE='\033[33m'; RD='\033[31m'
line(){ printf "%b\n" "${DIM}────────────────────────────────────────────────────────${C0}"; }
ok(){ printf "%b\n" "${GR}✔${C0} $*"; }
warn(){ printf "%b\n" "${YE}⚠${C0} $*"; }
err(){ printf "%b\n" "${RD}✖${C0} $*"; }
info(){ printf "%b\n" "${CY}▶${C0} $*"; }
ensure_root(){ [ "$(id -u)" -eq 0 ] || { err "请使用 root 运行 TR。"; exit 1; }; }

create_service() {
  ensure_root
  [ -x "$VENV_PY" ] || { err "缺少虚拟环境: $VENV_PY"; exit 1; }
  # 删除旧的双服务
  for old_svc in "${SERVICE_PREFIX}-admin" "${SERVICE_PREFIX}-core"; do
    if [ -f "/etc/systemd/system/${old_svc}.service" ]; then
      systemctl stop "$old_svc" 2>/dev/null || true
      systemctl disable "$old_svc" 2>/dev/null || true
      rm -f "/etc/systemd/system/${old_svc}.service"
      info "已清理旧服务: $old_svc"
    fi
  done
  cat >"$SVC_FILE" <<SERVICE
[Unit]
Description=TG-Radar Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$VENV_PY $APP_DIR/src/radar.py
Restart=always
RestartSec=5
TimeoutStopSec=180
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$SRC_DIR

[Install]
WantedBy=multi-user.target
SERVICE
  systemctl daemon-reload
  systemctl enable "$SVC" >/dev/null 2>&1 || true
  ok "systemd 服务已写入: $SVC"
}

status_view() {
  line
  printf "%b\n" "${B}TG-Radar · 控制台${C0}"
  printf "%b\n" "${DIM}单进程 · 事件驱动 · 全解耦插件${C0}"
  line
  printf "项目目录  %s\n" "$APP_DIR"
  printf "插件目录  %s\n" "$PLUGINS_REPO_DIR"
  printf "服务名    %s\n\n" "$SVC"
  systemctl status "$SVC" --no-pager -l 2>/dev/null || warn "$SVC 状态获取失败"
}

start_svc(){ ensure_root; systemctl start "$SVC"; ok "服务已启动。"; }
stop_svc(){ ensure_root; systemctl stop "$SVC" || true; ok "服务已停止。"; }
restart_svc(){ ensure_root; systemctl restart "$SVC"; ok "服务已重启。"; }
sync_once(){ cd "$APP_DIR"; PYTHONPATH="$SRC_DIR" "$PY" "$SRC_DIR/sync_once.py"; }
reauth(){ cd "$APP_DIR"; PYTHONPATH="$SRC_DIR" "$PY" "$SRC_DIR/bootstrap_session.py" </dev/tty; }

show_logs() {
  local target="${1:-all}"
  case "$target" in
    all|radar) journalctl -u "$SVC" -n 100 --no-pager ;;
    admin) journalctl -u "$SVC" -n 100 --no-pager ;;
    core) journalctl -u "$SVC" -n 100 --no-pager ;;
    *) err "未知: $target"; exit 1 ;;
  esac
}

doctor() {
  line
  printf "%b\n" "${B}TG-Radar · 环境自检${C0}"
  line
  [ -x "$VENV_PY" ] && ok "Python: $VENV_PY" || err "缺少 venv"
  [ -d "$SRC_DIR/tgr" ] && ok "源码: $SRC_DIR/tgr" || err "缺少源码"
  [ -f "$APP_DIR/config.json" ] && ok "配置: config.json" || err "缺少配置"
  [ -d "$PLUGINS_REPO_DIR/plugins" ] && ok "插件: $PLUGINS_REPO_DIR" || warn "插件目录不存在"
  [ -f "$APP_DIR/runtime/radar.db" ] && ok "数据库: radar.db" || warn "数据库未生成"
  [ -f "$APP_DIR/runtime/sessions/tg_radar_admin.session" ] && ok "Session 已存在" || warn "缺少 session"
  systemctl is-enabled "$SVC" >/dev/null 2>&1 && ok "$SVC 已启用" || warn "$SVC 未启用"
  systemctl is-active "$SVC" >/dev/null 2>&1 && ok "$SVC 运行中" || warn "$SVC 未运行"
  [ -x /usr/local/bin/TR ] && ok "TR 命令已注册" || warn "TR 命令不存在"
  local ver
  ver="$(PYTHONPATH="$SRC_DIR" "$PY" -c 'from tgr.version import __version__; print(__version__)' 2>/dev/null || echo '?')"
  ok "版本: $ver"
}

update_repo() {
  ensure_root
  if [ ! -d "$APP_DIR/.git" ]; then err "非 Git 仓库。"; exit 1; fi
  info "更新核心仓库"
  git -C "$APP_DIR" pull --ff-only
  if [ -d "$PLUGINS_REPO_DIR/.git" ]; then
    info "更新插件仓库"
    git -C "$PLUGINS_REPO_DIR" pull --ff-only
  fi
  if [ -f "$APP_DIR/requirements.txt" ]; then "$PIP" install -r "$APP_DIR/requirements.txt" >/dev/null 2>&1; fi
  if [ -f "$PLUGINS_REPO_DIR/requirements.txt" ]; then "$PIP" install -r "$PLUGINS_REPO_DIR/requirements.txt" >/dev/null 2>&1 || true; fi
  create_service
  restart_svc
  ok "更新完成。"
}

menu() {
  clear
  line
  printf "%b\n" "${B}TG-Radar · Terminal Radar${C0}"
  printf "%b\n" "${DIM}单进程 · 事件驱动 · 全解耦插件${C0}"
  line
  cat <<'MENU'
 1) 写入 / 刷新 systemd 服务
 2) 启动服务
 3) 停止服务
 4) 重启服务
 5) 查看服务状态
 6) 查看日志
 7) 执行一次同步
 8) 重新授权 Telegram
 9) 环境自检
10) 更新核心与插件仓库
 0) 退出
MENU
  line
}

case "${1:-menu}" in
  install-services|install-service) create_service ;;
  start) start_svc ;;
  stop) stop_svc ;;
  restart) restart_svc ;;
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
        1) create_service ;; 2) start_svc ;; 3) stop_svc ;; 4) restart_svc ;; 5) status_view ;;
        6) show_logs ;; 7) sync_once ;; 8) reauth ;; 9) doctor ;; 10) update_repo ;; 0) exit 0 ;;
        *) warn "无效选项。" ;;
      esac
      echo
      if [ -r /dev/tty ]; then read -rp "按回车返回菜单..." _dummy </dev/tty; else read -rp "按回车返回菜单..." _dummy; fi
    done ;;
  *)
    line
    printf "%b\n" "${B}TG-Radar · 终端管理器${C0}"
    line
    cat <<USAGE
用法:
  TR              交互菜单
  TR status       服务状态
  TR start        启动
  TR stop         停止
  TR restart      重启
  TR sync         手动同步
  TR reauth       重新授权
  TR logs         查看日志
  TR doctor       环境自检
  TR update       更新代码
USAGE
    exit 1 ;;
esac
