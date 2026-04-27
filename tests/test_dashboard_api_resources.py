#!/usr/bin/env python3
"""Tests for dashboard resources API routes."""

from unittest.mock import patch, MagicMock

import pytest
from flask import Flask

from dashboard import dashboard_bp, _sessions
import dashboard.api  # noqa: F401
from dashboard.config_store import ConfigStore


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


def _make_mock_resource(**kwargs):
    defaults = {
        "unique_id": "aws:ec2:us-east-1:i-123",
        "resource_type": "ec2",
        "name": "test1",
        "id": "i-123",
        "status": "running",
        "meta": {},
        "tags": {},
    }
    defaults.update(kwargs)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def _make_mock_metrics(**kwargs):
    defaults = {
        "sparkline_7d": [10.0, 20.0],
        "current": 20.0,
        "stats_7d": {"avg": None, "p95": None, "max": None},
        "stats_30d": {"avg": None, "p95": None, "max": None},
    }
    defaults.update(kwargs)
    m = MagicMock()
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


@patch("dashboard.api.get_provider")
def test_get_resources(mock_get_provider, auth_client):
    mock_provider = mock_get_provider.return_value
    mock_provider.name = "aws"
    mock_provider.regions.return_value = ["us-east-1"]
    mock_provider.resource_types.return_value = ["ec2"]
    mock_provider.discover_resources.return_value = [
        _make_mock_resource(),
    ]
    mock_provider.get_metrics.return_value = _make_mock_metrics()
    resp = auth_client.get("/api/dashboard/resources")
    assert resp.status_code == 200
    assert resp.json["ok"] is True
    assert len(resp.json["resources"]) == 1
    assert resp.json["resources"][0]["id"] == "aws:ec2:us-east-1:i-123"


@patch("dashboard.api.get_provider")
def test_get_resources_filter_by_type(mock_get_provider, auth_client):
    mock_provider = mock_get_provider.return_value
    mock_provider.name = "aws"
    mock_provider.regions.return_value = ["us-east-1"]
    mock_provider.resource_types.return_value = ["ec2", "rds"]
    mock_provider.discover_resources.return_value = [
        _make_mock_resource(unique_id="aws:ec2:us-east-1:i-123", resource_type="ec2", name="test1", id="i-123"),
        _make_mock_resource(unique_id="aws:rds:us-east-1:my-db", resource_type="rds", name="my-db", id="my-db"),
    ]
    mock_provider.get_metrics.return_value = _make_mock_metrics(sparkline_7d=[], current=None)
    resp = auth_client.get("/api/dashboard/resources?type=ec2")
    assert resp.status_code == 200
    assert len(resp.json["resources"]) == 1
    assert resp.json["resources"][0]["type"] == "ec2"


def test_get_pins(auth_client, monkeypatch, tmp_path):
    mappings_file = tmp_path / "dashboard_config.json"
    original_init = ConfigStore.__init__

    def patched_init(self, env_path=".env", mappings_path="dashboard_config.json"):
        original_init(self, env_path=env_path, mappings_path=str(mappings_file))

    monkeypatch.setattr("dashboard.api.ConfigStore.__init__", patched_init)

    resp = auth_client.get("/api/dashboard/resources/pins")
    assert resp.status_code == 200
    assert resp.json == {"ok": True, "pins": []}


def test_set_pins(auth_client, monkeypatch, tmp_path):
    mappings_file = tmp_path / "dashboard_config.json"
    original_init = ConfigStore.__init__

    def patched_init(self, env_path=".env", mappings_path="dashboard_config.json"):
        original_init(self, env_path=env_path, mappings_path=str(mappings_file))

    monkeypatch.setattr("dashboard.api.ConfigStore.__init__", patched_init)

    resp = auth_client.post("/api/dashboard/resources/pins", json={"pins": ["ec2:i-123"]})
    assert resp.status_code == 200
    assert resp.json == {"ok": True}

    resp = auth_client.get("/api/dashboard/resources/pins")
    assert resp.json["pins"] == ["ec2:i-123"]
