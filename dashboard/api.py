#!/usr/bin/env python3
"""Dashboard API routes for agents, skills, config, and mappings."""

import json
import os
import time
from flask import jsonify, request

from dashboard import dashboard_bp, require_auth
from dashboard.kiro_scanner import (
    list_agents,
    list_skills,
    get_skill_content,
    create_skill,
    delete_skill,
    get_agent_skills,
    add_skill_to_agent,
    remove_skill_from_agent,
)
from dashboard.config_store import ConfigStore, CORE_KEYS
from event_ingest import webhook_handler, ingest_to_store
from event_store import EventStore
from dashboard.providers import get_provider
from dashboard.metrics_store import MetricsStore


SENSITIVE_KEYS = {"WEBHOOK_TOKEN", "DASHBOARD_TOKEN"}

_resource_cache = {}
CACHE_TTL = 300


def _parse_provider_from_id(resource_id: str) -> str:
    first = resource_id.split(":", 1)[0]
    if first in ("aws", "tencent"):
        return first
    return "aws"


def _cache_key(provider_name: str) -> str:
    return f"resources:{provider_name}"


def _fetch_resources_for_provider(provider, refresh=False):
    key = _cache_key(provider.name)
    now = time.time()
    if not refresh and key in _resource_cache:
        data, ts = _resource_cache[key]
        if (now - ts) < CACHE_TTL:
            data["cached"] = True
            return data

    resources = []
    for region in provider.regions():
        for rtype in provider.resource_types():
            resources.extend(provider.discover_resources(region, rtype))

    store = MetricsStore()
    result_resources = []
    for resource in resources:
        # Prefer local SQLite; fallback to live cloud API if no local data
        try:
            hist_7d = store.query_history(resource.unique_id, "CPUUtilization", "7d")
            hist_30d = store.query_history(resource.unique_id, "CPUUtilization", "30d")
            sparkline = [d["value"] for d in hist_7d["data"]]
            current = sparkline[-1] if sparkline else None
            stats_7d = hist_7d["stats"]
            stats_30d = hist_30d["stats"]
            if not sparkline:
                raise ValueError("no local data")
        except Exception:
            metrics = provider.get_metrics(resource, range_days=7)
            sparkline = metrics.sparkline_7d
            current = metrics.current
            stats_7d = metrics.stats_7d or {"avg": None, "p95": None, "max": None}
            stats_30d = metrics.stats_30d or {"avg": None, "p95": None, "max": None}
        result_resources.append(
            {
                "id": resource.unique_id,
                "type": resource.resource_type,
                "name": resource.name,
                "raw_id": resource.id,
                "status": resource.status,
                "meta": {**resource.meta, "region": getattr(resource, "region", None) if isinstance(getattr(resource, "region", None), str) else resource.meta.get("region")},
                "class_type": getattr(resource, "class_type", None) if isinstance(getattr(resource, "class_type", None), str) else None,
                "os_or_engine": getattr(resource, "os_or_engine", None) if isinstance(getattr(resource, "os_or_engine", None), str) else None,
                "tags": resource.tags,
                "sparkline": sparkline,
                "current": current,
                "stats_7d": stats_7d,
                "stats_30d": stats_30d,
            }
        )
    store.close()

    data = {
        "resources": result_resources,
        "regions": provider.regions(),
        "cached": False,
        "error": None,
    }
    _resource_cache[key] = (data, now)
    return data


@dashboard_bp.route("/api/dashboard/agents", methods=["GET"])
@require_auth
def get_agents():
    return jsonify({"ok": True, "agents": list_agents()})


@dashboard_bp.route("/api/dashboard/skills", methods=["GET"])
@require_auth
def get_skills():
    return jsonify({"ok": True, "skills": list_skills()})


@dashboard_bp.route("/api/dashboard/skills", methods=["POST"])
@require_auth
def post_skill():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    description = (payload.get("description") or "").strip()

    if not name:
        return jsonify({"ok": False, "error": "name is required"}), 400

    ok = create_skill(name, description)
    if not ok:
        return jsonify({"ok": False, "error": f"Skill '{name}' already exists or name is invalid"}), 409
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/skills/<name>/content", methods=["GET"])
@require_auth
def get_skill_content_route(name):
    content = get_skill_content(name)
    if content is None:
        return jsonify({"ok": False, "error": f"Skill '{name}' not found"}), 404
    return jsonify({"ok": True, "content": content})


@dashboard_bp.route("/api/dashboard/skills/<name>", methods=["DELETE"])
@require_auth
def delete_skill_route(name):
    ok = delete_skill(name)
    if not ok:
        return jsonify({"ok": False, "error": f"Skill '{name}' not found"}), 404
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/agents/<name>/skills", methods=["GET"])
@require_auth
def get_agent_skills_route(name):
    skills = get_agent_skills(name)
    return jsonify({"ok": True, "skills": skills})


@dashboard_bp.route("/api/dashboard/agents/<name>/skills", methods=["POST"])
@require_auth
def add_agent_skill_route(name):
    payload = request.get_json(silent=True) or {}
    skill_name = (payload.get("skill_name") or "").strip()

    if not skill_name:
        return jsonify({"ok": False, "error": "skill_name is required"}), 400

    ok = add_skill_to_agent(name, skill_name)
    if not ok:
        return jsonify({"ok": False, "error": f"Agent '{name}' not found"}), 404
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/agents/<name>/skills/<skill_name>", methods=["DELETE"])
@require_auth
def remove_agent_skill_route(name, skill_name):
    ok = remove_skill_from_agent(name, skill_name)
    if not ok:
        return jsonify({"ok": False, "error": f"Agent '{name}' not found"}), 404
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/config", methods=["GET"])
@require_auth
def get_config():
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    cfg = store.read_core_config()
    for key in SENSITIVE_KEYS:
        if key in cfg:
            cfg[key] = "***"
    dashboard_cfg = store.load()
    providers_cfg = dashboard_cfg.get("providers", {})
    return jsonify({"ok": True, "config": cfg, "providers": providers_cfg})


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


@dashboard_bp.route("/api/dashboard/alert-defaults", methods=["GET"])
@require_auth
def get_alert_defaults():
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    return jsonify({"ok": True, "defaults": store.read_alert_defaults()})


@dashboard_bp.route("/api/dashboard/alert-defaults", methods=["POST"])
@require_auth
def post_alert_defaults():
    payload = request.get_json(silent=True) or {}
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    defaults = payload.get("defaults", {})
    store.write_alert_defaults(defaults)
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/reload-config", methods=["POST"])
@require_auth
def post_reload_config():
    try:
        from webhook_server import config_reloader
        config_reloader.force_reload()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
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
    if start_date:
        where_clauses.append("ts >= ?")
        params.append(start_date)
    if end_date:
        where_clauses.append("ts <= ?")
        params.append(end_date + "T23:59:59")

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


@dashboard_bp.route("/api/dashboard/resources", methods=["GET"])
@require_auth
def get_resources():
    refresh = request.args.get("refresh") == "1"
    resource_type = request.args.get("type", "")
    tag_key = request.args.get("tag_key", "")
    tag_value = request.args.get("tag_value", "")
    provider_name = request.args.get("provider", "aws")
    try:
        provider = get_provider(provider_name)
        data = _fetch_resources_for_provider(provider, refresh=refresh)
        resources = data.get("resources", [])
        if resource_type:
            resources = [r for r in resources if r["type"] == resource_type]
        if tag_key:
            resources = [r for r in resources if tag_key in (r.get("tags") or {})]
            if tag_value:
                resources = [r for r in resources if (r.get("tags") or {}).get(tag_key) == tag_value]
        store = ConfigStore()
        pins = store.read_pinned_resources()
        return jsonify({
            "ok": True,
            "resources": resources,
            "regions": data.get("regions", []),
            "pinned": pins,
            "cached": data.get("cached", False),
            "error": data.get("error"),
        })
    except Exception as e:
        return jsonify({"ok": True, "resources": [], "pinned": [], "error": str(e)}), 200


@dashboard_bp.route("/api/dashboard/resources/pins", methods=["GET"])
@require_auth
def get_resource_pins():
    store = ConfigStore()
    return jsonify({"ok": True, "pins": store.read_pinned_resources()})


@dashboard_bp.route("/api/dashboard/resources/pins", methods=["POST"])
@require_auth
def set_resource_pins():
    body = request.get_json(force=True) or {}
    pins = body.get("pins", [])
    store = ConfigStore()
    store.write_pinned_resources(pins)
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/resources/<path:resource_id>/history", methods=["GET"])
@require_auth
def get_resource_history(resource_id):
    metric = request.args.get("metric", "CPUUtilization")
    range_label = request.args.get("range", "24h")
    valid_ranges = {"24h", "7d", "30d", "180d"}
    if range_label not in valid_ranges:
        return jsonify({"ok": False, "error": f"Invalid range. Use one of: {', '.join(valid_ranges)}"}), 400

    provider_name = _parse_provider_from_id(resource_id)
    get_provider(provider_name)  # validate provider exists

    store = MetricsStore()
    try:
        result = store.query_history(resource_id, metric, range_label)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        store.close()
