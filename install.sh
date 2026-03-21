#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

REPO_URL_DEFAULT="https://github.com/chenmo8848/TG-Radar.git"
PLUGINS_REPO_URL_DEFAULT="https://github.com/chenmo8848/TG-Radar-Plugins.git"
TARGET_DIR_DEFAULT="/root/TG-Radar"
PYTHON_BIN_DEFAULT="python3"

REPO_URL="${REPO_URL:-$REPO_URL_DEFAULT}"
PLUGINS_REPO_URL="${PLUGINS_REPO_URL:-$PLUGINS_REPO_URL_DEFAULT}"
TARGET_DIR="${TARGET_DIR:-$TARGET_DIR_DEFAULT}"
PYTHON_BIN="${PYTHON_BIN:-$PYTHON_BIN_DEFAULT}"
PLUGINS_DIR="${PLUGINS_DIR:-$TARGET_DIR/plugins-external/TG-Radar-Plugins}"
SERVICE_PREFIX="${SERVICE_PREFIX:-tg-radar}"
BRANCH="${BRANCH:-main}"
PLUGINS_BRANCH="${PLUGINS_BRANCH:-main}"

log() {
  printf '\033[1;32m[TG-Radar]\033[0m %s\n' "$*"
}

warn() {
  printf '\033[1;33m[TG-Radar]\033[0m %s\n' "$*" >&2
}

die() {
  printf '\033[1;31m[TG-Radar]\033[0m %s\n' "$*" >&2
  exit 1
}

need_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "请用 root 执行。推荐：bash <(curl -fsSL https://raw.githubusercontent.com/chenmo8848/TG-Radar/main/install.sh)"
  fi
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

ensure_system_packages() {
  local missing=()
  have_cmd git || missing+=("git")
  have_cmd "$PYTHON_BIN" || missing+=("python3")
  "$PYTHON_BIN" -m venv --help >/dev/null 2>&1 || missing+=("python3-venv")

  if (( ${#missing[@]} == 0 )); then
    return 0
  fi

  if have_cmd apt-get; then
    log "安装系统依赖: ${missing[*]}"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y git curl python3 python3-venv python3-pip
  else
    die "缺少依赖: ${missing[*]}，且当前系统没有 apt-get。请先手工安装。"
  fi
}

clone_or_update_repo() {
  local repo_url="$1"
  local repo_dir="$2"
  local repo_branch="$3"

  if [[ -d "$repo_dir/.git" ]]; then
    log "更新仓库: $repo_dir"
    git -C "$repo_dir" fetch --all --tags --prune
    git -C "$repo_dir" checkout "$repo_branch"
    git -C "$repo_dir" pull --ff-only origin "$repo_branch"
    return 0
  fi

  if [[ -e "$repo_dir" && ! -d "$repo_dir/.git" ]]; then
    die "目标路径已存在但不是 Git 仓库: $repo_dir"
  fi

  log "克隆仓库: $repo_url -> $repo_dir"
  mkdir -p "$(dirname "$repo_dir")"
  git clone --depth 1 --branch "$repo_branch" "$repo_url" "$repo_dir"
}

ensure_config_file() {
  local repo_dir="$1"
  cd "$repo_dir"

  [[ -f "config.example.json" ]] || die "缺少 config.example.json"
  if [[ ! -f "config.json" ]]; then
    cp config.example.json config.json
    log "已生成 config.json"
  fi
}

prompt_if_needed() {
  local key="$1"
  local prompt_text="$2"
  local current_value="$3"

  if [[ -n "$current_value" ]]; then
    printf '%s' "$current_value"
    return 0
  fi

  if [[ -t 0 ]]; then
    local value=""
    read -r -p "$prompt_text" value
    printf '%s' "$value"
    return 0
  fi

  printf ''
}

patch_config() {
  local repo_dir="$1"
  cd "$repo_dir"

  local api_id_input="${API_ID:-}"
  local api_hash_input="${API_HASH:-}"

  local current_api_id current_api_hash
  current_api_id="$("$PYTHON_BIN" - <<'PY'
import json, pathlib
cfg = json.loads(pathlib.Path("config.json").read_text(encoding="utf-8"))
v = cfg.get("api_id")
print("" if v in (None, 0, 1234567) else v)
PY
)"
  current_api_hash="$("$PYTHON_BIN" - <<'PY'
import json, pathlib
cfg = json.loads(pathlib.Path("config.json").read_text(encoding="utf-8"))
v = str(cfg.get("api_hash") or "")
print("" if v in ("", "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx") else v)
PY
)"

  api_id_input="$(prompt_if_needed "API_ID" "请输入 Telegram API_ID: " "$api_id_input")"
  api_hash_input="$(prompt_if_needed "API_HASH" "请输入 Telegram API_HASH: " "$api_hash_input")"

  [[ -n "$api_id_input" ]] || api_id_input="$current_api_id"
  [[ -n "$api_hash_input" ]] || api_hash_input="$current_api_hash"

  [[ -n "$api_id_input" ]] || die "缺少 API_ID。可这样执行：API_ID=1234567 API_HASH=xxxx bash install.sh"
  [[ -n "$api_hash_input" ]] || die "缺少 API_HASH。可这样执行：API_ID=1234567 API_HASH=xxxx bash install.sh"

  "$PYTHON_BIN" - <<PY
import json, pathlib

cfg_path = pathlib.Path("config.json")
cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

cfg["api_id"] = int("${api_id_input}")
cfg["api_hash"] = "${api_hash_input}"
cfg["repo_url"] = "${REPO_URL}"
cfg["plugins_repo_url"] = "${PLUGINS_REPO_URL}"
cfg["plugins_dir"] = "./plugins-external/TG-Radar-Plugins"
cfg["service_name_prefix"] = "${SERVICE_PREFIX}"

cfg_path.write_text(
    json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

  log "config.json 已写入/更新"
}

setup_python_env() {
  local repo_dir="$1"
  local plugins_dir="$2"

  cd "$repo_dir"

  log "创建虚拟环境"
  "$PYTHON_BIN" -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate

  log "升级 pip"
  python -m pip install --upgrade pip wheel setuptools

  log "安装核心依赖"
  pip install -r requirements.txt

  if [[ -f "$plugins_dir/requirements.txt" ]]; then
    log "安装插件依赖"
    pip install -r "$plugins_dir/requirements.txt"
  fi
}

prepare_runtime_layout() {
  local repo_dir="$1"
  local plugins_dir="$2"

  mkdir -p "$repo_dir/runtime/logs" \
           "$repo_dir/runtime/sessions" \
           "$repo_dir/runtime/plugins" \
           "$repo_dir/plugins-external"

  if [[ -L "$repo_dir/runtime/plugins/TG-Radar-Plugins" || -e "$repo_dir/runtime/plugins/TG-Radar-Plugins" ]]; then
    rm -rf "$repo_dir/runtime/plugins/TG-Radar-Plugins"
  fi

  ln -s "$plugins_dir" "$repo_dir/runtime/plugins/TG-Radar-Plugins"
}

need_bootstrap() {
  local repo_dir="$1"
  [[ ! -f "$repo_dir/runtime/sessions/tg_radar_admin.session" ]] \
  || [[ ! -f "$repo_dir/runtime/sessions/tg_radar_admin_worker.session" ]] \
  || [[ ! -f "$repo_dir/runtime/sessions/tg_radar_core.session" ]]
}

run_bootstrap() {
  local repo_dir="$1"
  cd "$repo_dir"
  # shellcheck disable=SC1091
  source .venv/bin/activate

  log "开始 Telegram 首次授权"
  python src/bootstrap_session.py
}

run_initial_sync() {
  local repo_dir="$1"
  cd "$repo_dir"
  # shellcheck disable=SC1091
  source .venv/bin/activate

  log "执行首次同步"
  python src/sync_once.py
}

deploy_services() {
  local repo_dir="$1"
  cd "$repo_dir"

  log "写入并启用 systemd"
  bash deploy.sh
  systemctl daemon-reload

  local prefix
  prefix="$("$PYTHON_BIN" - <<'PY'
import json, pathlib
cfg = json.loads(pathlib.Path("config.json").read_text(encoding="utf-8"))
print(cfg.get("service_name_prefix") or "tg-radar")
PY
)"
  systemctl enable --now "${prefix}-admin" "${prefix}-core"
  systemctl restart "${prefix}-admin" "${prefix}-core"

  log "服务状态："
  systemctl --no-pager --full status "${prefix}-admin" || true
  systemctl --no-pager --full status "${prefix}-core" || true
}

main() {
  need_root
  ensure_system_packages

  clone_or_update_repo "$REPO_URL" "$TARGET_DIR" "$BRANCH"
  clone_or_update_repo "$PLUGINS_REPO_URL" "$PLUGINS_DIR" "$PLUGINS_BRANCH"

  ensure_config_file "$TARGET_DIR"
  patch_config "$TARGET_DIR"
  prepare_runtime_layout "$TARGET_DIR" "$PLUGINS_DIR"
  setup_python_env "$TARGET_DIR" "$PLUGINS_DIR"

  if need_bootstrap "$TARGET_DIR"; then
    run_bootstrap "$TARGET_DIR"
  else
    log "检测到现有 session，跳过首次授权"
  fi

  run_initial_sync "$TARGET_DIR"
  deploy_services "$TARGET_DIR"

  cat <<EOF

安装完成。

核心目录:
  $TARGET_DIR

插件目录:
  $PLUGINS_DIR

后续更新:
  cd $TARGET_DIR && git pull
  cd $PLUGINS_DIR && git pull
  cd $TARGET_DIR && bash deploy.sh && systemctl restart ${SERVICE_PREFIX}-admin ${SERVICE_PREFIX}-core

EOF
}

main "$@"
