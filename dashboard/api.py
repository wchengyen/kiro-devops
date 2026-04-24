#!/usr/bin/env python3
"""Dashboard API routes for agents, skills, config, and mappings."""

import json
import os
from flask import jsonify, request

from dashboard import dashboard_bp, require_auth
from dashboard.kiro_scanner import list_agents, list_skills
from dashboard.config_store import ConfigStore, CORE_KEYS
from event_ingest import webhook_handler, ingest_to_store
from event_store import EventStore


SENSITIVE_KEYS = {"WEBHOOK_TOKEN", "DASHBOARD_TOKEN"}


@dashboard_bp.route("/api/dashboard/agents", methods=["GET"])
@require_auth
def get_agents():
    return jsonify({"ok": True, "agents": list_agents()})


@dashboard_bp.route("/api/dashboard/skills", methods=["GET"])
@require_auth
def get_skills():
    return jsonify({"ok": True, "skills": list_skills()})


@dashboard_bp.route("/api/dashboard/config", methods=["GET"])
@require_auth
def get_config():
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    cfg = store.read_core_config()
    for key in SENSITIVE_KEYS:
        if key in cfg:
            cfg[key] = "***"
    return jsonify({"ok": True, "config": cfg})


@dashboard_bp.route("/api/dashboard/config", methods=["POST"])
@require_auth
def post_config():
    payload = request.get_json(silent=True) or {}
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    updates = {k: v for k, v in payload.items() if k in CORE_KEYS}
    if updates:
        store.write_core_config(updates)
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/mappings", methods=["GET"])
@require_auth
def get_mappings():
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    mappings = store.read_mappings()
    return jsonify({"ok": True, "mappings": mappings})


@dashboard_bp.route("/api/dashboard/mappings", methods=["POST"])
@require_auth
def post_mappings():
    payload = request.get_json(silent=True) or {}
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    mappings = payload.get("mappings", [])
    store.write_mappings(mappings)
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/service-rules", methods=["GET"])
@require_auth
def get_service_rules():
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    rules = store.read_service_rules()
    return jsonify({"ok": True, "rules": rules})


@dashboard_bp.route("/api/dashboard/service-rules", methods=["POST"])
@require_auth
def post_service_rules():
    payload = request.get_json(silent=True) or {}
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    rules = payload.get("rules", [])
    store.write_service_rules(rules)
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/events", methods=["GET"])
@require_auth
def get_events():
    source = request.args.get("source")
    severity = request.args.get("severity")
    event_type = request.args.get("event_type")
    q = request.args.get("q")
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)

    where_clauses = []
    params = []

    if source:
        where_clauses.append("source = ?")
        params.append(source)
    if severity:
        where_clauses.append("severity = ?")
        params.append(severity)
    if event_type:
        where_clauses.append("event_type = ?")
        params.append(event_type)
    if q:
        where_clauses.append("(title LIKE ? OR description LIKE ?)")
        params.append(f"%{q}%")
        params.append(f"%{q}%")

    sql = "SELECT * FROM events"
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    store = EventStore()
    with store._conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        events = []
        for row in rows:
            d = dict(row)
            d["entities"] = json.loads(d["entities"])
            events.append(d)

    return jsonify({"ok": True, "events": events})


@dashboard_bp.route("/api/dashboard/events", methods=["POST"])
@require_auth
def post_event():
    payload = request.get_json(silent=True) or {}
    default_user_id = os.environ.get("ALERT_NOTIFY_USER_ID", "system")
    record = webhook_handler(payload, default_user_id=default_user_id)
    if not record.get("ok"):
        return jsonify(record), 400
    result = ingest_to_store(EventStore(), record)
    if not result.get("ok"):
        status = 500 if result.get("error", "").startswith("内部错误") else 400
        return jsonify(result), status
    return jsonify(result)


@dashboard_bp.route("/api/dashboard/events/<event_id>", methods=["DELETE"])
@require_auth
def delete_event(event_id):
    store = EventStore()
    with store._conn() as conn:
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.commit()
    return jsonify({"ok": True})


# ---- Scheduler CRUD ----

@dashboard_bp.route("/api/dashboard/scheduler", methods=["GET"])
@require_auth
def get_scheduler():
    from scheduler import Scheduler

    sched = Scheduler(send_fn=lambda *a, **k: None, kiro_fn=lambda *a, **k: "")
    return jsonify({"ok": True, "jobs": sched.list_jobs("all")})


@dashboard_bp.route("/api/dashboard/scheduler", methods=["POST"])
@require_auth
def post_scheduler():
    from scheduler import Scheduler

    body = request.get_json(silent=True) or {}
    sched = Scheduler(send_fn=lambda *a, **k: None, kiro_fn=lambda *a, **k: "")
    result = sched.add_job(
        user_id=body.get("user_id", "system"),
        frequency=body.get("frequency", "每天"),
        time_str=body.get("time_str", "09:00"),
        prompt=body.get("prompt", ""),
    )
    return jsonify({"ok": True, "job_id": result})


@dashboard_bp.route("/api/dashboard/scheduler/<int:job_id>", methods=["PUT"])
@require_auth
def put_scheduler(job_id):
    from scheduler import Scheduler

    body = request.get_json(silent=True) or {}
    sched = Scheduler(send_fn=lambda *a, **k: None, kiro_fn=lambda *a, **k: "")
    if "enabled" in body:
        if body["enabled"]:
            sched.enable_job(job_id)
        else:
            sched.disable_job(job_id)
    if any(k in body for k in ("frequency", "time_str", "prompt")):
        sched.edit_job(job_id, body)
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/scheduler/<int:job_id>", methods=["DELETE"])
@require_auth
def delete_scheduler(job_id):
    from scheduler import Scheduler

    sched = Scheduler(send_fn=lambda *a, **k: None, kiro_fn=lambda *a, **k: "")
    sched.delete_job(job_id)
    return jsonify({"ok": True})
