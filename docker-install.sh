#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

REPO_URL="https://github.com/chenmo8848/TG-Radar.git"
PLUGINS_REPO_URL="https://github.com/chenmo8848/TG-Radar-Plugins.git"
INSTALL_DIR="${TR_INSTALL_DIR:-/root/TG-Radar}"
BRANCH="main"

C0='\033[0m'; B='\033[1m'; DIM='\033[2m'; CY='\033[36m'; GR='\033[32m'; YE='\033[33m'; RD='\033[31m'
step(){ printf "%b\n" "${CY}▶${C0} $*"; }
ok(){ printf "%b\n" "${GR}✔${C0} $*"; }
err(){ printf "%b\n" "${RD}✖${C0} $*"; }
line(){ printf "%b\n" "${DIM}────────────────────────────────────────────────────────${C0}"; }
die(){ err "$*"; exit 1; }
read_tty(){ local __var="$1" __prompt="$2" __value=""
  if [ -r /dev/tty ]; then read -r -p "$__prompt" __value </dev/tty || true
  else read -r -p "$__prompt" __value || true; fi
  printf -v "$__var" '%s' "$__value"
}

line
printf "%b\n" "${B}TG-Radar · Docker 一键部署${C0}"
printf "%b\n" "${DIM}全解耦插件架构 · 单进程 · 事件驱动${C0}"
line

# ── 检查 root ──
[ "$(id -u)" -eq 0 ] || die "请使用 root 执行安装脚本。"

# ── 安装 Docker ──
if command -v docker &>/dev/null; then
  ok "Docker 已安装"
else
  step "安装 Docker ..."
  curl -fsSL https://get.docker.com | sh >/dev/null 2>&1
  ok "Docker 安装完成"
fi

# 确保 Docker 运行中
if ! docker info &>/dev/null; then
  systemctl start docker 2>/dev/null || service docker start 2>/dev/null || true
  sleep 2
  docker info &>/dev/null || die "Docker 无法启动，请检查安装。"
fi

# ── 安装 git ──
if ! command -v git &>/dev/null; then
  step "安装 git ..."
  apt-get update -y >/dev/null 2>&1 && apt-get install -y git >/dev/null 2>&1 \
    || yum install -y git >/dev/null 2>&1 \
    || die "无法安装 git，请手动安装后重试。"
  ok "git 已安装"
fi

# ── 拉取仓库 ──
if [ -d "$INSTALL_DIR/.git" ]; then
  ok "检测到已有项目目录：$INSTALL_DIR"
  step "拉取最新代码 ..."
  git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || true
else
  step "拉取核心仓库 ..."
  git clone --depth=1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi
ok "核心仓库已就绪"

PLUGINS_DIR="$INSTALL_DIR/plugins-external/TG-Radar-Plugins"
if [ -d "$PLUGINS_DIR/.git" ]; then
  step "拉取插件最新代码 ..."
  git -C "$PLUGINS_DIR" pull --ff-only 2>/dev/null || true
else
  step "拉取插件仓库 ..."
  git clone --depth=1 --branch "$BRANCH" "$PLUGINS_REPO_URL" "$PLUGINS_DIR"
fi
ok "插件仓库已就绪"

cd "$INSTALL_DIR"

# ── 创建必要目录 ──
mkdir -p runtime/logs/plugins runtime/sessions runtime/backups configs

# ── 配置 API 凭据 ──
step "准备配置文件"
[ -f config.json ] || cp config.example.json config.json

need_creds=0
python3 - <<'PY' 2>/dev/null || need_creds=1
import json, sys
d = json.loads(open("config.json").read())
api_id = d.get("api_id")
api_hash = d.get("api_hash", "")
if api_id in (None, 0, 1234567) or api_hash == "x" * 32:
    sys.exit(1)
PY

if [ "$need_creds" -eq 1 ]; then
  echo ""
  printf "%b\n" "${YE}获取方式：访问 https://my.telegram.org → API development tools${C0}"
  echo ""
  read_tty API_ID "请输入 Telegram API_ID: "
  read_tty API_HASH "请输入 Telegram API_HASH: "
  [ -n "${API_ID:-}" ] || die "缺少 API_ID"
  [ -n "${API_HASH:-}" ] || die "缺少 API_HASH"
  python3 - <<PY
import json, pathlib
p = pathlib.Path("config.json")
d = json.loads(p.read_text())
d["api_id"] = int("${API_ID}")
d["api_hash"] = "${API_HASH}"
d["plugins_dir"] = "./plugins-external/TG-Radar-Plugins/plugins"
p.write_text(json.dumps(d, ensure_ascii=False, indent=4) + "\n")
PY
fi
ok "config.json 已就绪"

# ── 构建镜像 ──
step "构建 Docker 镜像 ..."
docker compose build --no-cache 2>&1 | tail -1
ok "镜像构建完成"

# ── Telegram 授权 ──
if [ ! -f "runtime/sessions/tg_radar_admin.session" ]; then
  line
  printf "%b\n" "${B}首次授权 Telegram 账号${C0}"
  printf "%b\n" "${DIM}接下来需要输入手机号、验证码、二步验证密码（如有）。${C0}"
  line
  echo ""
  docker compose run --rm tg-radar auth
  ok "Telegram 授权完成"
else
  ok "检测到现有会话，跳过授权"
fi

# ── 首次同步 ──
step "执行首次同步 ..."
docker compose run --rm tg-radar sync
ok "首次同步完成"

# ── 启动服务 ──
step "启动服务 ..."
docker compose up -d
ok "服务已启动"

line
printf "%b\n" "${B}TG-Radar · Docker 部署完成 ✔${C0}"
line
echo ""
printf "  项目目录   %s\n" "$INSTALL_DIR"
printf "  插件目录   %s\n" "$PLUGINS_DIR"
echo ""
line
echo "  常用命令："
echo "    docker compose logs -f       # 查看日志"
echo "    docker compose restart       # 重启"
echo "    docker compose down          # 停止"
echo "    docker compose up -d         # 启动"
echo ""
echo "  Telegram 命令："
echo "    -help / -status / -plugins / -folders / -update"
line
