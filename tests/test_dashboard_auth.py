#!/usr/bin/env python3
"""Dashboard auth middleware tests"""

import pytest
from flask import Flask

from dashboard import dashboard_bp, DASHBOARD_TOKEN, _sessions, require_auth


@pytest.fixture
def client(monkeypatch):
    """Create a test client with the dashboard blueprint registered."""
    monkeypatch.setattr("dashboard.DASHBOARD_TOKEN", "test-secret-token")
    _sessions.clear()

    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    with app.test_client() as c:
        yield c
    _sessions.clear()


@pytest.fixture
def client_with_empty_token(monkeypatch):
    """Create a test client with empty DASHBOARD_TOKEN."""
    monkeypatch.setattr("dashboard.DASHBOARD_TOKEN", "")
    _sessions.clear()

    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)

    with app.test_client() as c:
        yield c

    _sessions.clear()


def test_auth_login_success(client):
    resp = client.post(
        "/api/dashboard/auth",
        json={"token": "test-secret-token"},
    )
    assert resp.status_code == 200
    cookies = [h for h in resp.headers.getlist("Set-Cookie") if "dashboard_session" in h]
    assert len(cookies) >= 1
    assert "HttpOnly" in cookies[0]


def test_auth_login_failure(client):
    resp = client.post(
        "/api/dashboard/auth",
        json={"token": "wrong-token"},
    )
    assert resp.status_code == 401


def test_protected_route_without_auth():
    """Access /api/dashboard/agents without cookie → 401"""
    app = Flask(__name__)

    @app.route("/api/dashboard/agents", methods=["GET"])
    @require_auth
    def dummy_agents():
        return {"agents": []}

    _sessions.clear()

    with app.test_client() as c:
        resp = c.get("/api/dashboard/agents")
        assert resp.status_code == 401

    _sessions.clear()


def test_auth_empty_token_returns_503(client_with_empty_token):
    resp = client_with_empty_token.post(
        "/api/dashboard/auth",
        json={"token": "anything"},
    )
    assert resp.status_code == 503
    assert b"Dashboard not configured" in resp.data


def test_auth_logout(client):
    # Log in first
    resp = client.post(
        "/api/dashboard/auth",
        json={"token": "test-secret-token"},
    )
    assert resp.status_code == 200

    # Log out
    resp = client.post("/api/dashboard/logout")
    assert resp.status_code == 200
    assert resp.json == {"ok": True}

    # Verify session cookie is cleared
    cookies = [h for h in resp.headers.getlist("Set-Cookie") if "dashboard_session" in h]
    assert len(cookies) >= 1
    cookie_val = cookies[0]
    assert cookie_val.startswith("dashboard_session=;") or "expires=0" in cookie_val.lower()


def test_protected_route_with_auth():
    """Access a protected route after login → 200"""
    app = Flask(__name__)

    @app.route("/api/dashboard/agents", methods=["GET"])
    @require_auth
    def dummy_agents():
        return {"agents": []}

    _sessions.clear()

    with app.test_client() as c:
        # Log in to create a session
        _sessions["test-session-id"] = {
            "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        c.set_cookie("dashboard_session", "test-session-id")

        resp = c.get("/api/dashboard/agents")
        assert resp.status_code == 200
        assert resp.json == {"agents": []}

    _sessions.clear()
