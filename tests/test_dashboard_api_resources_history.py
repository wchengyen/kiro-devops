#!/usr/bin/env python3
"""Tests for dashboard resources history API route."""

from unittest.mock import patch

import pytest
from flask import Flask

from dashboard import dashboard_bp, _sessions
import dashboard.api  # noqa: F401


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("dashboard.DASHBOARD_TOKEN", "test-secret-token")
    _sessions.clear()
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    with app.test_client() as c:
        yield c
    _sessions.clear()


@pytest.fixture
def auth_client(client):
    resp = client.post("/api/dashboard/auth", json={"token": "test-secret-token"})
    assert resp.status_code == 200
    return client


@patch("dashboard.api.MetricsStore")
def test_get_resource_history_24h(mock_store_cls, auth_client):
    mock_store = mock_store_cls.return_value
    mock_store.query_history.return_value = {
        "resource_id": "ec2:cn-north-1:i-123",
        "metric": "cpu_utilization",
        "range": "24h",
        "granularity": "hourly",
        "data": [{"timestamp": 1714113600, "value": 12.5}],
        "stats": {"min": 5.0, "avg": 12.5, "p95": 20.0, "max": 30.0},
    }

    resp = auth_client.get("/api/dashboard/resources/ec2:cn-north-1:i-123/history?range=24h")
    assert resp.status_code == 200
    assert resp.json["ok"] is True
    assert resp.json["granularity"] == "hourly"
    assert resp.json["data"][0]["value"] == 12.5
    assert resp.json["stats"]["avg"] == 12.5
    mock_store.close.assert_called_once()
