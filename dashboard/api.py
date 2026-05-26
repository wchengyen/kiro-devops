#!/usr/bin/env python3
"""Dashboard API routes for agents, skills, config, and mappings."""

import json
import os
import shutil
import subprocess
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
from dashboard.cost_scoring import (
    compute_cost_score,
    get_cost_grade,
    get_cost_advice,
    get_hourly_price,
    compute_waste_cost,
    grade_color,
)
import uuid
import threading
from datetime import datetime, timezone
from dashboard.resource_tree_store import ResourceTreeStore
from dashboard.resource_tree import ResourceTreeBuilder, AWSResourceScanner


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
        cpu_avg = stats_7d.get("avg") if stats_7d else None
        cost_score = compute_cost_score(cpu_avg)
        cost_grade = get_cost_grade(cost_score)
        hourly_price = get_hourly_price(resource.resource_type, getattr(resource, "class_type", None))
        cost_breakdown = compute_waste_cost(hourly_price, cost_score)
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
                "cost_score": cost_score,
                "cost_grade": cost_grade,
                "cost_advice": get_cost_advice(cpu_avg),
                "cost_color": grade_color(cost_grade),
                "cost_breakdown": cost_breakdown,
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


@dashboard_bp.route("/api/dashboard/models", methods=["GET"])
@require_auth
def list_models():
    """Return available models from kiro-cli --list-models."""
    kiro_bin = shutil.which("kiro-cli") or "/home/ubuntu/.local/bin/kiro-cli"
    try:
        result = subprocess.run(
            [kiro_bin, "chat", "--list-models", "--format", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return jsonify(data)
    except Exception as e:
        return jsonify({"models": [], "default_model": None, "error": str(e)}), 500

    return jsonify({"models": [], "default_model": None, "error": "kiro-cli failed"}), 500


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

_scan_jobs: dict[str, dict] = {}
_scan_jobs_lock = threading.Lock()
_DEFAULT_TREE_DB = os.path.join(os.path.dirname(__file__), "..", "memory_db", "resource_tree.db")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "dashboard_config.json")


def _get_tree_store():
    return ResourceTreeStore(_DEFAULT_TREE_DB)


def _load_dashboard_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def _save_dashboard_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


@dashboard_bp.route("/api/dashboard/resource-tree/config", methods=["GET"])
@require_auth
def get_resource_tree_config():
    config = _load_dashboard_config()
    tree_config = config.get("resource_tree", {
        "group_by_tags": [],
        "layout_algorithm": "cose",
        "default_provider": "aws",
    })
    return jsonify({"ok": True, "config": tree_config})


@dashboard_bp.route("/api/dashboard/resource-tree/config", methods=["POST"])
@require_auth
def post_resource_tree_config():
    body = request.get_json() or {}
    config = _load_dashboard_config()
    tree_config = config.get("resource_tree", {})
    if "group_by_tags" in body:
        tree_config["group_by_tags"] = body["group_by_tags"]
    if "layout_algorithm" in body:
        tree_config["layout_algorithm"] = body["layout_algorithm"]
    config["resource_tree"] = tree_config
    _save_dashboard_config(config)
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/resource-tree/graph", methods=["GET"])
@require_auth
def get_resource_tree_graph():
    provider_name = request.args.get("provider", "aws")
    tag_key = request.args.get("tag_key", "")
    tag_value = request.args.get("tag_value", "")
    config = _load_dashboard_config()
    tree_config = config.get("resource_tree", {})
    group_by_tags = tree_config.get("group_by_tags", [])

    try:
        provider = get_provider(provider_name)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    resources = []
    if provider and provider.is_enabled():
        for region in provider.regions():
            for rtype in provider.resource_types():
                try:
                    resources.extend(provider.discover_resources(region, rtype))
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(
                        f"discover_resources failed for {provider_name}/{region}/{rtype}: {e}"
                    )

    store = _get_tree_store()
    relations = store.get_relations(provider=provider_name)
    positions = store.get_positions()

    builder = ResourceTreeBuilder()
    graph = builder.build_graph(
        resources, relations, group_by_tags, positions,
        tag_key=tag_key or None,
        tag_value=tag_value or None,
    )
    return jsonify({"ok": True, **graph})


@dashboard_bp.route("/api/dashboard/resource-tree/scan", methods=["POST"])
@require_auth
def post_resource_tree_scan():
    body = request.get_json() or {}
    provider_name = body.get("provider", "aws")
    if provider_name != "aws":
        return jsonify({"ok": False, "error": "Auto-scan only supported for AWS"}), 400

    job_id = str(uuid.uuid4())
    with _scan_jobs_lock:
        _scan_jobs[job_id] = {"status": "running", "count": 0, "error": None}
        # Prune old completed jobs, keeping only the most recent 50
        completed = [jid for jid, j in _scan_jobs.items() if j.get("status") in ("done", "failed")]
        if len(completed) > 50:
            for old_jid in sorted(completed)[:len(completed) - 50]:
                _scan_jobs.pop(old_jid, None)

    def _do_scan():
        try:
            config = _load_dashboard_config()
            regions = config.get("providers", {}).get(provider_name, {}).get("regions", [])
            if not regions:
                regions = config.get("regions", [])

            store = _get_tree_store()
            store.clear_auto_scan_relations(provider_name)

            scanner = AWSResourceScanner()
            relations = scanner.scan(regions)
            for rel in relations:
                store.add_relation(
                    source_id=rel["source_id"],
                    target_id=rel["target_id"],
                    relation_type=rel["relation_type"],
                    source_origin="auto_scan",
                    provider=provider_name,
                )
            with _scan_jobs_lock:
                _scan_jobs[job_id]["status"] = "done"
                _scan_jobs[job_id]["count"] = len(relations)
        except Exception as e:
            with _scan_jobs_lock:
                _scan_jobs[job_id]["status"] = "failed"
                _scan_jobs[job_id]["error"] = str(e)

    threading.Thread(target=_do_scan, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@dashboard_bp.route("/api/dashboard/resource-tree/scan/<job_id>", methods=["GET"])
@require_auth
def get_resource_tree_scan_status(job_id):
    with _scan_jobs_lock:
        job = _scan_jobs.get(job_id, {"status": "unknown", "count": 0, "error": None})
    return jsonify({"ok": True, **job})


@dashboard_bp.route("/api/dashboard/resource-tree/relations", methods=["POST"])
@require_auth
def post_resource_tree_relation():
    body = request.get_json() or {}
    source_id = body.get("source_id")
    target_id = body.get("target_id")
    relation_type = body.get("relation_type", "depends_on")
    provider = body.get("provider", "aws")
    if not source_id or not target_id:
        return jsonify({"ok": False, "error": "source_id and target_id required"}), 400

    store = _get_tree_store()
    rid = store.add_relation(
        source_id=source_id,
        target_id=target_id,
        relation_type=relation_type,
        source_origin="manual",
        provider=provider,
    )
    return jsonify({"ok": True, "id": rid})


@dashboard_bp.route("/api/dashboard/resource-tree/relations/<relation_id>", methods=["DELETE"])
@require_auth
def delete_resource_tree_relation(relation_id):
    store = _get_tree_store()
    relations = store.get_relations()
    target = next((r for r in relations if r["id"] == relation_id), None)
    if not target:
        return jsonify({"ok": False, "error": "Not found"}), 404
    if target["source_origin"] == "auto_scan":
        return jsonify({"ok": False, "error": "Cannot delete auto_scan relation"}), 403
    store.delete_relation(relation_id)
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/resource-tree/positions", methods=["PUT"])
@require_auth
def put_resource_tree_positions():
    body = request.get_json() or {}
    positions = body.get("positions", {})
    store = _get_tree_store()
    store.save_positions(positions)
    return jsonify({"ok": True})
