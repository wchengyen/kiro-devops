#!/bin/bash
# 掃描服務日誌與 Dashboard API response 是否洩漏 Secret 或 AWS credential（規格 §16、§22.12）
# 用法：bash scripts/secret_leak_scan.sh [分鐘數，預設 60]
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."
SINCE="${1:-60}"

if [ -f .env ]; then
    set -a; source .env; set +a
fi

FAILED=0
scan() {  # scan <名稱> <pattern>（grep -F 固定字串）
    local name="$1" pattern="$2"
    [ -z "$pattern" ] && return 0
    if journalctl -u kiro-devops --since "${SINCE} minutes ago" --no-pager 2>/dev/null \
        | grep -qF "$pattern"; then
        echo "FAIL  ${name} 出現在 journalctl"
        FAILED=1
    else
        echo "PASS  ${name} 未出現在 journalctl"
    fi
}

scan "FEISHU_APP_SECRET" "${FEISHU_APP_SECRET:-}"
scan "WEBHOOK_TOKEN" "${WEBHOOK_TOKEN:-}"
scan "DASHBOARD_TOKEN" "${DASHBOARD_TOKEN:-}"
scan "AWS_ACCESS_KEY_ID 值" "${AWS_ACCESS_KEY_ID:-}"
scan "AWS_SECRET_ACCESS_KEY 值" "${AWS_SECRET_ACCESS_KEY:-}"
scan "AWS_SESSION_TOKEN 值" "${AWS_SESSION_TOKEN:-}"

# 通用 credential 形態掃描（不限於 .env 值）
if journalctl -u kiro-devops --since "${SINCE} minutes ago" --no-pager 2>/dev/null \
    | grep -qE "AKIA[0-9A-Z]{16}|aws_secret_access_key\s*="; then
    echo "FAIL  日誌出現 AWS credential 形態"
    FAILED=1
else
    echo "PASS  日誌無 AWS credential 形態"
fi

# Dashboard response 掃描（需 DASHBOARD_TOKEN；未啟用則略過）
PORT="${WEBHOOK_PORT:-8080}"
if [ -n "${DASHBOARD_TOKEN:-}" ]; then
    BODY=$(curl -s --max-time 10 -H "X-Dashboard-Token: ${DASHBOARD_TOKEN}" \
        "http://127.0.0.1:${PORT}/dashboard/api/config" 2>/dev/null || true)
    LEAK=0
    for pattern in "${FEISHU_APP_SECRET:-}" "${WEBHOOK_TOKEN:-}" "${AWS_SECRET_ACCESS_KEY:-}"; do
        [ -n "$pattern" ] && echo "$BODY" | grep -qF "$pattern" && LEAK=1
    done
    if [ "$LEAK" -eq 1 ]; then
        echo "FAIL  Dashboard /api/config response 含 Secret"
        FAILED=1
    else
        echo "PASS  Dashboard /api/config response 無 Secret"
    fi
else
    echo "SKIP  DASHBOARD_TOKEN 未設定，略過 Dashboard response 掃描"
fi

exit "$FAILED"
