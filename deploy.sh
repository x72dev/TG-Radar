#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_PREFIX="$(python3 - <<'PY'
import json, pathlib
cfg = json.loads(pathlib.Path('config.json').read_text(encoding='utf-8'))
print(cfg.get('service_name_prefix', 'tg-radar'))
PY
)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
ADMIN_UNIT="/etc/systemd/system/${SERVICE_PREFIX}-admin.service"
CORE_UNIT="/etc/systemd/system/${SERVICE_PREFIX}-core.service"
cat > "$ADMIN_UNIT" <<UNIT
[Unit]
Description=TG-Radar Admin
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT_DIR
ExecStart=$PYTHON_BIN $ROOT_DIR/src/radar_admin.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
cat > "$CORE_UNIT" <<UNIT
[Unit]
Description=TG-Radar Core
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT_DIR
ExecStart=$PYTHON_BIN $ROOT_DIR/src/radar_core.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now "${SERVICE_PREFIX}-admin.service" "${SERVICE_PREFIX}-core.service"
echo "[OK] Services deployed: ${SERVICE_PREFIX}-admin / ${SERVICE_PREFIX}-core"
