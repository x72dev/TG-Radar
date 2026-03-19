#!/bin/bash
# ============================================================
#  TG-Radar  --  一键安装脚本 v5.1.1
# ============================================================
set -euo pipefail

# --- 1. 变量与环境配置 ---
REPO="chenmo8848/TG-Radar"
INSTALL_DIR="/root/TG-Radar"
GLOBAL_CMD="/usr/local/bin/TGR"

# 颜色定义
BOLD='\033[1m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RESET='\033[0m'

# --- 2. 欢迎界面 ---
echo -e "\n${BOLD}  ============================================================${RESET}"
echo -e "${BOLD}     TG-Radar  --  Telegram 关键词监听雷达  --  安装程序 v5.1.1      ${RESET}"
echo -e "${BOLD}  ============================================================${RESET}\n"

# --- 3. 权限与依赖检查 ---
if [ "$(id -u)" -ne 0 ]; then 
    echo -e "  ${YELLOW}[警告] 需要 root 权限。${RESET}"
    exit 1
fi

for cmd in curl unzip python3; do 
    if ! command -v "$cmd" > /dev/null 2>&1; then
        echo "  [错误] 缺少工具：$cmd"
        exit 1
    fi
done

# --- 4. 获取最新版本信息 ---
echo -e "  ${CYAN}==>${RESET} 正在获取最新版本信息..."

if ! RELEASE_JSON=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest"); then
    echo "  [错误] 无法访问 GitHub API"
    exit 1
fi

VERSION=$(echo "$RELEASE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])")
DOWNLOAD_URL=$(echo "$RELEASE_JSON" | python3 -c "import sys,json; print([a['browser_download_url'] for a in json.load(sys.stdin).get('assets',[]) if a['name'].endswith('.zip')][0])" 2>/dev/null || echo "")

if [ -z "$DOWNLOAD_URL" ]; then 
    echo "  [错误] 未找到 .zip 附件。"
    exit 1
fi

# --- 5. 备份现有配置 ---
RESTORE_CONFIG=false
if [ -f "$INSTALL_DIR/config.json" ]; then
    cp "$INSTALL_DIR/config.json" /tmp/tg_radar_config.bak
    echo -e "  ${YELLOW}[提示] 已备份现有 config.json，安装后将自动还原。${RESET}"
    RESTORE_CONFIG=true
fi

# --- 6. 下载与部署 ---
echo -e "  ${CYAN}==>${RESET} 正在下载与部署..."
mkdir -p "$INSTALL_DIR"

if ! curl -fsSL "$DOWNLOAD_URL" -o /tmp/TG_Radar_release.zip; then
    echo "  [错误] 下载失败。"
    exit 1
fi

unzip -q -o /tmp/TG_Radar_release.zip -d "$INSTALL_DIR"
rm -f /tmp/TG_Radar_release.zip

# 恢复配置
if [ "$RESTORE_CONFIG" = true ]; then 
    cp /tmp/tg_radar_config.bak "$INSTALL_DIR/config.json"
    rm -f /tmp/tg_radar_config.bak
fi

chmod +x "$INSTALL_DIR/deploy.sh"

# --- 7. 配置全局快捷命令 ---
cat > "$GLOBAL_CMD" << 'TGREOF'
#!/bin/bash
exec bash /root/TG-Radar/deploy.sh "$@"
TGREOF

chmod +x "$GLOBAL_CMD"

# --- 8. 完成与启动 ---
echo -e "\n  ${GREEN}[完成]${RESET} TG-Radar ${VERSION} 安装成功！2 秒后自动打开管理菜单...\n"
sleep 2
exec bash "$INSTALL_DIR/deploy.sh"