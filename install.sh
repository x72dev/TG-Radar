#!/bin/bash
# ============================================================
#  TG-Radar  --  Installer
#  https://github.com/chenmo8848/TG-Radar
#
#  Usage:
#    bash <(curl -fsSL https://raw.githubusercontent.com/chenmo8848/TG-Radar/main/install.sh)
# ============================================================
set -euo pipefail

REPO="chenmo8848/TG-Radar"
INSTALL_DIR="/root/TG-Radar"
GLOBAL_CMD="/usr/local/bin/TGR"

BOLD='\033[1m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RESET='\033[0m'

echo ""
echo -e "${BOLD}  ============================================================${RESET}"
echo -e "${BOLD}     TG-Radar  --  Telegram Keyword Monitor  --  Installer    ${RESET}"
echo -e "${BOLD}  ============================================================${RESET}"
echo ""

# Root check
if [ "$(id -u)" -ne 0 ]; then
    echo -e "  ${YELLOW}[!!] Must be run as root. Try: sudo bash install.sh${RESET}"
    exit 1
fi

# Dependency check
for cmd in curl unzip python3; do
    command -v "$cmd" > /dev/null 2>&1 || { echo "  [ERR] Missing: $cmd"; exit 1; }
done

# Fetch latest release info from GitHub API
echo -e "  ${CYAN}-->${RESET} Fetching latest release info..."
RELEASE_JSON=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest") \
    || { echo "  [ERR] Failed to reach GitHub API. Check network."; exit 1; }

# Parse: get tag name and the first .zip asset download URL
VERSION=$(echo "$RELEASE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])")
DOWNLOAD_URL=$(echo "$RELEASE_JSON" | python3 -c "
import sys, json
assets = json.load(sys.stdin).get('assets', [])
zips = [a['browser_download_url'] for a in assets if a['name'].endswith('.zip')]
print(zips[0] if zips else '')
")

if [ -z "$DOWNLOAD_URL" ]; then
    echo "  [ERR] No .zip asset found in latest release (${VERSION})."
    echo "        Please check: https://github.com/${REPO}/releases"
    exit 1
fi

echo -e "  ${CYAN}-->${RESET} Latest release: ${BOLD}${VERSION}${RESET}"
echo -e "  ${CYAN}-->${RESET} Asset: $(basename "$DOWNLOAD_URL")"

# Backup existing config if already customized
RESTORE_CONFIG=false
if [ -f "$INSTALL_DIR/config.json" ]; then
    API_ID=$(python3 -c "import json; print(json.load(open('$INSTALL_DIR/config.json')).get('api_id',''))" 2>/dev/null || echo "")
    if [ -n "$API_ID" ] && [ "$API_ID" != "1234567" ]; then
        cp "$INSTALL_DIR/config.json" /tmp/tg_radar_config.bak
        echo -e "  ${YELLOW}[!!] Existing config.json backed up (api_id=$API_ID).${RESET}"
        RESTORE_CONFIG=true
    fi
fi

# Download
echo -e "  ${CYAN}-->${RESET} Downloading..."
curl -fsSL "$DOWNLOAD_URL" -o /tmp/TG_Radar_release.zip \
    || { echo "  [ERR] Download failed."; exit 1; }

# Extract
echo -e "  ${CYAN}-->${RESET} Extracting to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
unzip -q -o /tmp/TG_Radar_release.zip -d "$INSTALL_DIR"
rm -f /tmp/TG_Radar_release.zip

# Restore user config
if [ "$RESTORE_CONFIG" = true ]; then
    cp /tmp/tg_radar_config.bak "$INSTALL_DIR/config.json"
    rm -f /tmp/tg_radar_config.bak
    echo -e "  ${GREEN}[OK]${RESET} config.json restored."
fi

chmod +x "$INSTALL_DIR/deploy.sh"

# Register global command TGR
echo -e "  ${CYAN}-->${RESET} Registering global command 'TGR'..."
cat > "$GLOBAL_CMD" << 'TGREOF'
#!/bin/bash
exec bash /root/TG-Radar/deploy.sh "$@"
TGREOF
chmod +x "$GLOBAL_CMD"

echo ""
echo -e "  ${GREEN}[OK]${RESET} TG-Radar ${VERSION} installed."
echo ""
echo -e "  Install path : ${BOLD}$INSTALL_DIR${RESET}"
echo -e "  Global cmd   : ${BOLD}TGR${RESET}"
echo ""
echo -e "  ${BOLD}Next steps:${RESET}"
echo -e "    1. Edit config : nano $INSTALL_DIR/config.json"
echo -e "    2. Open menu   : TGR  -->  option 1 (Deploy), then 6 (Authorize)"
echo ""
echo -e "  Opening management menu in 2 seconds..."
sleep 2

exec bash "$INSTALL_DIR/deploy.sh"
