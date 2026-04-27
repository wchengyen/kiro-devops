#!/usr/bin/env python3
"""Tests for Tencent dashboard resources API routes."""

from unittest.mock import patch, MagicMock

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


@patch("dashboard.api.get_provider")
def test_get_resources_tencent(mock_get_provider, auth_client):
    mock_provider = mock_get_provider.return_value
    mock_provider.name = "tencent"
    mock_provider.regions.return_value = ["ap-tokyo"]
    mock_provider.discover_resources.return_value = []
    resp = auth_client.get("/api/dashboard/resources?provider=tencent")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["regions"] == ["ap-tokyo"]


@patch("dashboard.api.get_provider")
def test_history_parses_tencent_id(mock_get_provider, auth_client):
    mock_provider = mock_get_provider.return_value
    mock_provider.get_metrics.return_value = MagicMock(points_7d=[])
    resp = auth_client.get("/api/dashboard/resources/tencent:cvm:ap-tokyo:ins-1/history?range=24h")
    assert resp.status_code == 200
    mock_get_provider.assert_called_once_with("tencent")
