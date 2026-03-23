#!/bin/bash
set -e

# ── Config: support both mounted file and env vars ──
if [ ! -f /app/config.json ]; then
    if [ -f /app/config-host/config.json ]; then
        cp /app/config-host/config.json /app/config.json
    elif [ -n "$API_ID" ] && [ -n "$API_HASH" ]; then
        echo "▶ 使用环境变量生成 config.json ..."
        python3 -c "
import json, pathlib
tpl = json.loads(pathlib.Path('/app/config.example.json').read_text())
tpl['api_id'] = int('${API_ID}')
tpl['api_hash'] = '${API_HASH}'
tpl['plugins_dir'] = './plugins-external/TG-Radar-Plugins/plugins'
pathlib.Path('/app/config.json').write_text(json.dumps(tpl, ensure_ascii=False, indent=4) + '\n')
"
        echo "✔ config.json 已生成"
    else
        echo "✖ 缺少 config.json 且未设置 API_ID / API_HASH 环境变量"
        echo "  请挂载 config.json 到 /app/config-host/config.json 或设置环境变量后重试。"
        exit 1
    fi
fi

# ── Seed: copy host runtime data into named volume on first run ──
if [ -d /app/runtime-host ] && [ ! -f /app/runtime/.seeded ]; then
    echo "▶ 从宿主机导入 runtime 数据 ..."
    cp -a /app/runtime-host/. /app/runtime/ 2>/dev/null || true
    touch /app/runtime/.seeded
    echo "✔ runtime 数据已导入"
fi

# ── Ensure dirs ──
mkdir -p /app/runtime/logs/plugins /app/runtime/sessions /app/runtime/backups /app/configs

# ── Clean stale SQLite locks ──
find /app/runtime -name "*-journal" -delete 2>/dev/null || true

case "${1:-run}" in
    auth|bootstrap)
        echo ""
        echo "TG-Radar · Telegram 授权"
        echo "──────────────────────────────────────────────────"
        echo "请按提示输入手机号、验证码、二步验证密码（如有）。"
        echo ""
        exec python3 /app/src/bootstrap_session.py
        ;;
    sync)
        echo "▶ 执行首次同步 ..."
        exec python3 /app/src/sync_once.py
        ;;
    run)
        if [ -f /app/runtime/sessions/tg_radar_admin.session ]; then
            echo "▶ 启动 TG-Radar ..."
            exec python3 /app/src/radar.py
        else
            echo "✖ 未找到 Telegram 会话文件。"
            echo "  请先运行授权: docker compose run --rm tg-radar auth"
            exit 1
        fi
        ;;
    *)
        exec "$@"
        ;;
esac
