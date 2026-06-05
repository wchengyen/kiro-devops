#!/usr/bin/env python3
"""Webhook HTTP 服务（告警接收 + Dashboard）."""
import json
import logging
import os
import threading
import time

from flask import Flask, request, jsonify

from dashboard import dashboard_bp
from alert_analysis import run_alert_analysis, config_reloader

log = logging.getLogger("webhook-server")
webhook_app = Flask("kiro-ec2-webhook")
webhook_app.register_blueprint(dashboard_bp)

# ---- 告警去重缓存 ----
# Alertmanager 会在告警持续期间周期性重推 webhook，需在内存层去重
_processed_alert_ids: set[str] = set()
_MAX_ALERT_ID_CACHE = 5000

# 基于 (alertname, instance, status) 的滑动窗口去重（处理 startsAt 微秒差异导致的不同 event_id）
_alert_window_cache: dict[tuple[str, str, str], float] = {}
_ALERT_DEDUP_WINDOW_SEC = 300  # 5 分钟窗口


def _is_duplicate_alert(record: dict) -> bool:
    """检查是否为重复告警推送。"""
    event_id = record.get("event_id", "")
    if event_id in _processed_alert_ids:
        log.info(f"告警去重(event_id): {event_id}")
        return True
    _processed_alert_ids.add(event_id)
    if len(_processed_alert_ids) > _MAX_ALERT_ID_CACHE:
        half = list(_processed_alert_ids)[_MAX_ALERT_ID_CACHE // 2:]
        _processed_alert_ids.clear()
        _processed_alert_ids.update(half)

    # 窗口去重：同一 alert + instance + status 在 5 分钟内只处理一次
    labels = record.get("entities", [])
    instance = labels[0] if labels else "unknown"
    alert_key = (record.get("source", "prometheus"), instance, record.get("event_type", ""))
    now = time.time()
    last = _alert_window_cache.get(alert_key, 0)
    if now - last <= _ALERT_DEDUP_WINDOW_SEC:
        log.info(f"告警去重(5min窗口): {alert_key}")
        return True
    _alert_window_cache[alert_key] = now
    return False


def _parse_alertmanager(payload: dict) -> dict:
    alert = payload["alerts"][0]
    labels = {**payload.get("commonLabels", {}), **alert.get("labels", {})}
    ann = {**payload.get("commonAnnotations", {}), **alert.get("annotations", {})}
    instance = labels.get("instance", "unknown").split(":")[0]
    is_resolved = alert.get("status") == "resolved"
    return {
        "ok": True,
        "event_id": f"prom-{labels.get('alertname', 'unknown')}-{alert['startsAt'][:19]}-{'resolved' if is_resolved else 'firing'}",
        "user_id": os.environ.get("ALERT_NOTIFY_USER_ID", "system"),
        "event_type": "故障处理" if is_resolved else "指标异常",
        "title": f"{'[RESOLVED] ' if is_resolved else ''}{ann.get('summary', labels.get('alertname'))}",
        "description": ann.get("description", ""),
        "entities": [instance, labels.get("job", "")] if labels.get("job") else [instance],
        "source": "prometheus",
        "severity": labels.get("severity", "medium"),
        "timestamp": alert.get("endsAt") if is_resolved else alert["startsAt"],
        "_raw_labels": labels,
    }


def _resolve_alert_targets() -> list[str]:
    """解析告警推送目标列表."""
    targets = os.environ.get("ALERT_NOTIFY_TARGETS", "").strip()
    if targets:
        return [t.strip() for t in targets.split(",") if t.strip()]
    legacy = os.environ.get("ALERT_NOTIFY_USER_ID", "").strip()
    if legacy:
        return [f"feishu:{legacy}"]
    return []


def create_routes(handler):
    """创建路由，绑定 MessageHandler 用于告警分析回调."""

    @webhook_app.route("/event", methods=["POST"])
    def receive_event():
        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {os.environ.get('WEBHOOK_TOKEN', '')}"
        if auth != expected:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

        payload = request.get_json(silent=True) or {}

        if "alerts" in payload:
            record = _parse_alertmanager(payload)
        else:
            from event_ingest import webhook_handler
            default_user = os.environ.get("ALERT_NOTIFY_USER_ID", "system")
            record = webhook_handler(payload, default_user_id=default_user)

        if not record.get("ok"):
            return jsonify(record), 400

        try:
            from event_store import EventStore
            event_store = EventStore()
            from event_ingest import ingest_to_store
            result = ingest_to_store(event_store, record)
            if not result["ok"]:
                return jsonify(result), 500
        except Exception:
            log.warning("事件存储不可用，跳过入库")

        auto_severities = os.environ.get("ALERT_AUTO_ANALYZE_SEVERITY", "high,critical").split(",")
        should_analyze = record.get("severity") in auto_severities

        if should_analyze and not _is_duplicate_alert(record):
            threading.Thread(
                target=_trigger_analysis,
                args=(handler, record),
                daemon=True,
                name=f"kiro-alert-{record['event_id'][:8]}"
            ).start()
        elif should_analyze:
            should_analyze = False

        return jsonify({
            "ok": True,
            "event_id": record["event_id"],
            "analysis_triggered": should_analyze
        }), 200

    @webhook_app.route("/health", methods=["GET"])
    def health():
        return jsonify({
            "status": "ok",
            "memory_enabled": os.environ.get("ENABLE_MEMORY", "false").lower() in ("true", "1", "yes"),
            "webhook": True,
        })


def _trigger_analysis(handler, record: dict):
    """触发 Kiro skill 分析并推送到所有配置目标."""
    targets = _resolve_alert_targets()

    log.info(f"触发 Kiro 告警分析: {record['title'][:50]}...")
    try:
        message, agent = run_alert_analysis(record)
        log.info(f"告警分析完成 (agent={agent})，推送到 {len(targets)} 个目标")
    except Exception as e:
        log.exception("Kiro 分析失败")
        message = f"❌ 告警分析失败: {e}"

    for target in targets:
        try:
            handler.dispatcher.send(target, message)
        except Exception as e:
            log.error(f"告警推送到 {target} 失败: {e}")


def start_webhook_server(handler, host: str = "127.0.0.1", port: int = 8080):
    create_routes(handler)
    threading.Thread(
        target=lambda: webhook_app.run(host=host, port=port, threaded=True),
        daemon=True,
        name="webhook-http"
    ).start()
    log.info(f"🌐 Webhook HTTP 监听 {host}:{port}")
