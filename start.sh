#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# 飞书可选，微信可扫码
if [ -z "$FEISHU_APP_ID" ] && [ -z "$WEIXIN_BOT_TOKEN" ] && [ ! -f "$HOME/.kiro/weixin_token.json" ]; then
    echo "⚠️  未配置任何平台（飞书或微信），请检查 .env"
    exit 1
fi

echo "🚀 启动 kiro-devops gateway（飞书 + 微信 + Webhook）"
if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
fi
python3 gateway.py
