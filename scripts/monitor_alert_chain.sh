#!/bin/bash
# 监控告警链路：Prometheus → Alertmanager → kiro-devops
# 用法：./monitor_alert_chain.sh &  然后在另一个终端执行 kubectl drain/delete node

echo "=== 告警链路监控启动 ==="
echo "时间戳               | 节点数 | NodeNotReady | NodeCountDropped | Alertmanager告警数 | kiro-event日志"
echo "------------------------------------------------------------------------------------------"

while true; do
    TS=$(date '+%H:%M:%S')

    # 节点数
    NODE_COUNT=$(kubectl get nodes --no-headers 2>/dev/null | wc -l)

    # Prometheus 告警状态
    PROM_ALERTS=$(curl -s 'http://localhost:9090/api/v1/alerts' 2>/dev/null)
    NODE_NOTREADY=$(echo "$PROM_ALERTS" | python3 -c "import sys,json; d=json.load(sys.stdin); alerts=[a for a in d.get('data',{}).get('alerts',[]) if a['labels'].get('alertname')=='NodeNotReady']; print('FIRING' if any(a['state']=='firing' for a in alerts) else 'pending' if alerts else 'none')" 2>/dev/null || echo "err")
    NODE_DROPPED=$(echo "$PROM_ALERTS" | python3 -c "import sys,json; d=json.load(sys.stdin); alerts=[a for a in d.get('data',{}).get('alerts',[]) if a['labels'].get('alertname')=='NodeCountDropped']; print('FIRING' if any(a['state']=='firing' for a in alerts) else 'pending' if alerts else 'none')" 2>/dev/null || echo "err")

    # Alertmanager 告警数
    AM_COUNT=$(curl -s http://localhost:9093/api/v2/alerts 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d))" 2>/dev/null || echo "err")

    # kiro-devops 最近 5 秒内的 /event 请求数
    KIRO_EVENTS=$(tail -n 50 /home/ubuntu/kiro-devops/gateway.log 2>/dev/null | grep -c 'POST /event' || echo "0")

    printf "%s | %2d     | %-12s | %-16s | %-18s | %s\n" \
        "$TS" "$NODE_COUNT" "$NODE_NOTREADY" "$NODE_DROPPED" "$AM_COUNT" "$KIRO_EVENTS"

    sleep 2
done
