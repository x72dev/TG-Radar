#!/bin/bash
# ============================================================
# TG-Radar -- 核心一键安装向导 (Main Branch)
# ============================================================
set -euo pipefail

REPO="chenmo8848/TG-Radar"
INSTALL_DIR="/root/TG-Radar"
GLOBAL_CMD="/usr/local/bin/TGR"
COMMIT_FILE="$INSTALL_DIR/.commit_sha"

# 现代 CLI 色彩
B='\033[1m'
DIM='\033[2m'
RES='\033[0m'
MAIN='\033[36m'
TAG_OK='\033[42;30m'
TAG_ERR='\033[41;37m'
TAG_WARN='\033[43;30m'

echo -e "\n${MAIN}${B} ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ ${RES}"
echo -e "${B}                   TG-RADAR 核心一键安装向导                    ${RES}"
echo -e "${MAIN}${B} ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ ${RES}\n"

if [ "$(id -u)" -ne 0 ]; then 
    echo -e "  ${TAG_ERR} 警告 ${RES} 需要 root 权限运行此脚本。"
    exit 1
fi

echo -ne "  ${MAIN}⠋${RES} 正在检查环境依赖..."
for cmd in curl unzip python3; do 
    if ! command -v "$cmd" > /dev/null 2>&1; then
        echo -e "\n  ${TAG_ERR} 错误 ${RES} 缺少必要依赖工具：$cmd"
        exit 1
    fi
done
echo -e "\r  ${TAG_OK} 依赖 ${RES} 环境检查通过       "

echo -ne "  ${MAIN}⠋${RES} 正在获取最新主分支代码信息..."
API_RES=$(curl -fsSL --connect-timeout 5 "https://api.github.com/repos/${REPO}/commits/main") || { 
    echo -e "\n  ${TAG_ERR} 错误 ${RES} 无法访问 GitHub API，请检查网络。"
    exit 1
}
LATEST_SHA=$(echo "$API_RES" | python3 -c "import sys,json; print(json.load(sys.stdin).get('sha',''))" 2>/dev/null)

if [ -z "$LATEST_SHA" ]; then 
    echo -e "\n  ${TAG_ERR} 错误 ${RES} 无法解析最新 Commit SHA。"
    exit 1
fi
SHORT_SHA=${LATEST_SHA:0:7}
echo -e "\r  ${TAG_OK} 寻址 ${RES} 最新代码"

RESTORE_CONFIG=false
if [ -f "$INSTALL_DIR/config.json" ]; then
    cp "$INSTALL_DIR/config.json" /tmp/tg_radar_config.bak
    echo -e "  ${TAG_WARN} 提示 ${RES} 检测到现有配置，已自动备份。"
    RESTORE_CONFIG=true
fi

echo -ne "  ${MAIN}⠋${RES} 正在下载并解压源码..."
mkdir -p "$INSTALL_DIR"
rm -rf /tmp/tgr_main.zip /tmp/TG-Radar-main
curl -fsSL "https://github.com/${REPO}/archive/refs/heads/main.zip" -o /tmp/tgr_main.zip || { 
    echo -e "\n  ${TAG_ERR} 错误 ${RES} 代码包下载失败。"
    exit 1
}

unzip -q -o /tmp/tgr_main.zip -d /tmp/ >/dev/null 2>&1
cp -af /tmp/TG-Radar-main/. "$INSTALL_DIR/"
rm -rf /tmp/tgr_main.zip /tmp/TG-Radar-main
echo -e "\r  ${TAG_OK} 部署 ${RES} 核心文件覆写完成     "

# 还原配置
if [ "$RESTORE_CONFIG" = true ]; then 
    cp /tmp/tg_radar_config.bak "$INSTALL_DIR/config.json"
    rm -f /tmp/tg_radar_config.bak
fi

# 写入 Commit Hash 以对接部署管家
echo "$LATEST_SHA" > "$COMMIT_FILE"

chmod +x "$INSTALL_DIR/deploy.sh" "$INSTALL_DIR/install.sh" 2>/dev/null || true

# 注册全局命令
echo -ne "  ${MAIN}⠋${RES} 正在配置全局环境..."
cat > "$GLOBAL_CMD" << 'TGREOF'
#!/bin/bash
exec bash /root/TG-Radar/deploy.sh "$@"
TGREOF
chmod +x "$GLOBAL_CMD"
echo -e "\r  ${TAG_OK} 注册 ${RES} TGR 全局快捷命令完成 "

echo -e "\n  ${TAG_OK} 成功 ${RES} ${B}TG-Radar 核心安装已完成！${RES}"
echo -e "         正在唤起部署管家 (2 秒后)...\n"
sleep 2
exec bash "$INSTALL_DIR/deploy.sh"
