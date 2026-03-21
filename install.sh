#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
if [ ! -f config.json ]; then
  cp config.example.json config.json
  echo "[INFO] config.json created from config.example.json"
fi
chmod +x deploy.sh
cat <<MSG
[OK] Environment prepared.
Next steps:
  1. Edit config.json
  2. Run: source .venv/bin/activate
  3. Run: python src/bootstrap_session.py
  4. Run: ./deploy.sh
MSG
