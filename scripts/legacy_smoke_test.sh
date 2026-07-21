#!/bin/bash
# Legacy 模式 smoke test：服務存活、/health、Webhook /event、啟動日誌無錯誤
# 用法：bash scripts/legacy_smoke_test.sh
# 依賴：.env 中的 WEBHOOK_PORT（預設 8080）與 WEBHOOK_TOKEN
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

FAILED=0
check() {  # check <名稱> <0=通過>
    if [ "$2" -eq 0 ]; then
        echo "PASS  $1"
    else
        echo "FAIL  $1"
        FAILED=1
    fi
}

if [ -f .env ]; then
    set -a; source .env; set +a
fi
PORT="${WEBHOOK_PORT:-8080}"
HOST="${WEBHOOK_HOST:-127.0.0.1}"
[ "$HOST" = "0.0.0.0" ] && HOST="127.0.0.1"

# 1. systemd 服務存活
systemctl is-active --quiet kiro-devops
check "systemd kiro-devops is-active" $?

# 2. /health 回 200
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://${HOST}:${PORT}/health")
[ "$CODE" = "200" ]
check "GET /health -> 200（實際: ${CODE}）" $?

# 3. Webhook /event 未授權回 401
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -X POST \
    "http://${HOST}:${PORT}/event" -H 'Content-Type: application/json' -d '{}')
[ "$CODE" = "401" ]
check "POST /event 無 token -> 401（實際: ${CODE}）" $?

# 4. Webhook /event 帶 token、低嚴重度（不觸發分析）可入庫
CODE=$(curl -s -o /tmp/legacy_smoke_event.json -w '%{http_code}' --max-time 10 -X POST \
    "http://${HOST}:${PORT}/event" \
    -H "Authorization: Bearer ${WEBHOOK_TOKEN}" \
    -H 'Content-Type: application/json' \
    -d "{\"id\":\"smoke-$(date +%s)\",\"event_type\":\"手动记录\",\"source\":\"legacy-smoke\",\"title\":\"smoke test event\",\"severity\":\"low\",\"message\":\"smoke\"}")
[ "$CODE" = "200" ] && grep -q '"ok": *true' /tmp/legacy_smoke_event.json
check "POST /event 帶 token severity=low -> 200 ok" $?

# 5. 最近啟動日誌無 traceback / 啟動失敗
if journalctl -u kiro-devops --since "10 minutes ago" --no-pager 2>/dev/null | \
    grep -qE "Traceback|CRITICAL|Failed to start"; then
    check "journalctl 最近 10 分鐘無 Traceback/CRITICAL" 1
else
    check "journalctl 最近 10 分鐘無 Traceback/CRITICAL" 0
fi

# 6. MULTI_PROFILE_ENABLED 確認為 false（legacy smoke 只在 legacy 模式有效）
if [ "${MULTI_PROFILE_ENABLED:-false}" = "true" ]; then
    echo "SKIP  目前為 multi-profile 模式，legacy smoke 不適用"
    exit 2
fi

if [ "$FAILED" -eq 0 ]; then
    echo "== legacy smoke test 全部通過 =="
else
    echo "== legacy smoke test 有失敗項目 =="
fi
exit "$FAILED"
