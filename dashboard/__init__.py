#!/usr/bin/env python3
"""Dashboard blueprint with auth middleware."""

import hmac
import os
import uuid
from functools import wraps
from datetime import datetime, timezone, timedelta

from flask import (
    Blueprint,
    request,
    jsonify,
    make_response,
    send_from_directory,
)

dashboard_bp = Blueprint(
    "dashboard",
    __name__,
    static_folder="static",
    static_url_path="/dashboard/static",
)

DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")

_sessions: dict[str, dict] = {}


SESSION_TTL_HOURS = 24


def require_auth(f):
    """Decorator that checks Cookie dashboard_session against _sessions."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        session_id = request.cookies.get("dashboard_session")
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=SESSION_TTL_HOURS)

        # Prune expired sessions
        expired = [
            sid
            for sid, meta in _sessions.items()
            if datetime.fromisoformat(meta["created_at"]) < cutoff
        ]
        for sid in expired:
            del _sessions[sid]

        if not session_id or session_id not in _sessions:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)

    return wrapper


@dashboard_bp.route("/api/dashboard/auth", methods=["POST"])
def auth_login():
    if not DASHBOARD_TOKEN:
        return jsonify({"error": "Dashboard not configured"}), 503

    payload = request.get_json(silent=True) or {}
    token = payload.get("token", "")

    if not hmac.compare_digest(token, DASHBOARD_TOKEN):
        return jsonify({"error": "Unauthorized"}), 401

    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    resp = make_response(jsonify({"ok": True}))
    resp.set_cookie(
        "dashboard_session",
        session_id,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return resp


@dashboard_bp.route("/api/dashboard/logout", methods=["POST"])
def auth_logout():
    session_id = request.cookies.get("dashboard_session")
    if session_id and session_id in _sessions:
        del _sessions[session_id]

    resp = make_response(jsonify({"ok": True}))
    resp.set_cookie(
        "dashboard_session",
        "",
        httponly=True,
        samesite="Lax",
        path="/",
        expires=0,
    )
    return resp


@dashboard_bp.route("/dashboard/", methods=["GET"])
def dashboard_index():
    return send_from_directory(
        dashboard_bp.static_folder or "static",
        "index.html",
    )
