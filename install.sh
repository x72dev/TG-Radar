#!/bin/bash
# ============================================================
#  TG-Radar  --  一键安装脚本 v5.1.1
# ============================================================
set -euo pipefail

REPO="chenmo8848/TG-Radar"
INSTALL_DIR="/root/TG-Radar"
GLOBAL_CMD="/usr/local/bin/TGR"
BOLD='\033[1m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RESET='\033[0m'

echo -e "\n${BOLD}  ============================================================${RESET}"
echo -e "${BOLD}     TG-Radar  --  Telegram 关键词监听雷达  --  安装程序 v5.1.1      ${RESET}"
echo -e "${BOLD}  ============================================================${RESET}\n"

if [ "$(id -u)" -ne 0 ]; then echo -e "  ${YELLOW}[警告] 需要 root 权限。${RESET}"; exit 1; fi
for cmd in curl unzip python3; do command -v "$cmd" > /dev/null 2>&1 || { echo "  [错误] 缺少工具：$cmd"; exit 1; }; done

echo -e "  ${CYAN}==>${RESET} 正在获取最新版本信息..."
RELEASE_JSON=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest") || { echo "  [错误] 无法访问 GitHub API"; exit 1; }
VERSION=$(echo "$RELEASE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])")
DOWNLOAD_URL=$(echo "$RELEASE_JSON" | python3 -c "import sys,json; print([a['browser_download_url'] for a in json.load(sys.stdin).get('assets',[]) if a['name'].endswith('.zip')][0])" 2>/dev/null || echo "")

if [ -z "$DOWNLOAD_URL" ]; then echo "  [错误] 未找到 .zip 附件。"; exit 1; fi

RESTORE_CONFIG=false
if [ -f "$INSTALL_DIR/config.json" ]; then
    cp "$INSTALL_DIR/config.json" /tmp/tg_radar_config.bak
    echo -e "  ${YELLOW}[提示] 已备份现有 config.json，安装后将自动还原。${RESET}"
    RESTORE_CONFIG=true
fi

echo -e "  ${CYAN}==>${RESET} 正在下载与部署..."
mkdir -p "$INSTALL_DIR"
curl -fsSL "$DOWNLOAD_URL" -o /tmp/TG_Radar_release.zip || { echo "  [错误] 下载失败。"; exit 1; }
unzip -q -o /tmp/TG_Radar_release.zip -d "$INSTALL_DIR"
rm -f /tmp/TG_Radar_release.zip

if [ "$RESTORE_CONFIG" = true ]; then cp /tmp/tg_radar_config.bak "$INSTALL_DIR/config.json" && rm -f /tmp/tg_radar_config.bak; fi
chmod +x "$INSTALL_DIR/deploy.sh"

cat > "$GLOBAL_CMD" << 'TGREOF'
#!/bin/bash
exec bash /root/TG-Radar/deploy.sh "$@"
TGREOF
chmod +x "$GLOBAL_CMD"

echo -e "\n  ${GREEN}[完成]${RESET} TG-Radar ${VERSION} 安装成功！2 秒后自动打开管理菜单...\n"
sleep 2
exec bash "$INSTALL_DIR/deploy.sh"
