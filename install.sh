#!/bin/bash
# ============================================================
#  TG-Radar  --  一键安装脚本 v5.0.1
#  https://github.com/chenmo8848/TG-Radar
#
#  用法：
#    bash <(curl -fsSL https://raw.githubusercontent.com/chenmo8848/TG-Radar/main/install.sh)
# ============================================================
set -euo pipefail

REPO="chenmo8848/TG-Radar"
INSTALL_DIR="/root/TG-Radar"
GLOBAL_CMD="/usr/local/bin/TGR"

BOLD='\033[1m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RESET='\033[0m'

echo ""
echo -e "${BOLD}  ============================================================${RESET}"
echo -e "${BOLD}     TG-Radar  --  Telegram 关键词监听雷达  --  安装程序 v5.0.1      ${RESET}"
echo -e "${BOLD}  ============================================================${RESET}"
echo ""

# 权限检查
if [ "$(id -u)" -ne 0 ]; then
    echo -e "  ${YELLOW}[警告] 需要 root 权限。请使用：sudo bash install.sh${RESET}"
    exit 1
fi

# 依赖检查
for cmd in curl unzip python3; do
    command -v "$cmd" > /dev/null 2>&1 || { echo "  [错误] 缺少工具：$cmd"; exit 1; }
done

# 从 GitHub API 获取最新 Release 信息
echo -e "  ${CYAN}==>${RESET} 正在获取最新版本信息..."
RELEASE_JSON=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest") \
    || { echo "  [错误] 无法访问 GitHub API，请检查网络。"; exit 1; }

VERSION=$(echo "$RELEASE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])")
DOWNLOAD_URL=$(echo "$RELEASE_JSON" | python3 -c "
import sys, json
assets = json.load(sys.stdin).get('assets', [])
zips = [a['browser_download_url'] for a in assets if a['name'].endswith('.zip')]
print(zips[0] if zips else '')
")

if [ -z "$DOWNLOAD_URL" ]; then
    echo "  [错误] 最新 Release（${VERSION}）中未找到 .zip 附件。"
    echo "         请检查：https://github.com/${REPO}/releases"
    exit 1
fi

echo -e "  ${CYAN}==>${RESET} 最新版本：${BOLD}${VERSION}${RESET}"
echo -e "  ${CYAN}==>${RESET} 文件名：$(basename "$DOWNLOAD_URL")"

# 备份已有配置（如果已自定义）
RESTORE_CONFIG=false
if [ -f "$INSTALL_DIR/config.json" ]; then
    API_ID=$(python3 -c "import json; print(json.load(open('$INSTALL_DIR/config.json')).get('api_id',''))" 2>/dev/null || echo "")
    if [ -n "$API_ID" ] && [ "$API_ID" != "1234567" ]; then
        cp "$INSTALL_DIR/config.json" /tmp/tg_radar_config.bak
        echo -e "  ${YELLOW}[提示] 已备份现有 config.json（api_id=$API_ID），安装后将自动还原。${RESET}"
        RESTORE_CONFIG=true
    fi
fi

# 下载
echo -e "  ${CYAN}==>${RESET} 正在下载..."
curl -fsSL "$DOWNLOAD_URL" -o /tmp/TG_Radar_release.zip \
    || { echo "  [错误] 下载失败，请重试。"; exit 1; }

# 解压
echo -e "  ${CYAN}==>${RESET} 正在解压到 $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
unzip -q -o /tmp/TG_Radar_release.zip -d "$INSTALL_DIR"
rm -f /tmp/TG_Radar_release.zip

# 还原用户配置
if [ "$RESTORE_CONFIG" = true ]; then
    cp /tmp/tg_radar_config.bak "$INSTALL_DIR/config.json"
    rm -f /tmp/tg_radar_config.bak
    echo -e "  ${GREEN}[完成]${RESET} config.json 已还原。"
fi

chmod +x "$INSTALL_DIR/deploy.sh"

# 注册全局命令 TGR
echo -e "  ${CYAN}==>${RESET} 注册全局命令 TGR ..."
cat > "$GLOBAL_CMD" << 'TGREOF'
#!/bin/bash
exec bash /root/TG-Radar/deploy.sh "$@"
TGREOF
chmod +x "$GLOBAL_CMD"

echo ""
echo -e "  ${GREEN}[完成]${RESET} TG-Radar ${VERSION} 安装成功！"
echo ""
echo -e "  安装路径：${BOLD}$INSTALL_DIR${RESET}"
echo -e "  全局命令：${BOLD}TGR${RESET}"
echo ""
echo -e "  ${BOLD}接下来的步骤：${RESET}"
echo -e "    1. 编辑配置：nano $INSTALL_DIR/config.json"
echo -e "    2. 打开管理菜单：TGR  -->  选项 1（部署），然后选项 6（授权）"
echo ""
echo -e "  2 秒后自动打开管理菜单..."
sleep 2

exec bash "$INSTALL_DIR/deploy.sh"
