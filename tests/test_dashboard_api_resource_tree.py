import pytest
import json
import os
from flask import Flask
from dashboard import dashboard_bp, _sessions
import dashboard.api
from dashboard.config_store import ConfigStore


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr("dashboard.DASHBOARD_TOKEN", "test-secret-token")
    _sessions.clear()
    config_path = str(tmp_path / "dashboard_config.json")
    with open(config_path, "w") as f:
        json.dump({
            "providers": {"aws": {"enabled": True, "regions": ["cn-north-1"]}},
            "resource_tree": {"group_by_tags": ["Project"], "layout_algorithm": "cose"}
        }, f)
    monkeypatch.setattr("dashboard.api.CONFIG_PATH", config_path)
    monkeypatch.setattr("dashboard.resource_tree_store.os.makedirs", lambda *a, **k: None)
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


def test_get_resource_tree_config(auth_client):
    resp = auth_client.get("/api/dashboard/resource-tree/config")
    assert resp.status_code == 200
    data = resp.json
    assert data["ok"] is True
    assert "group_by_tags" in data["config"]


def test_post_resource_tree_config(auth_client):
    resp = auth_client.post(
        "/api/dashboard/resource-tree/config",
        json={"group_by_tags": ["Team"], "layout_algorithm": "grid"}
    )
    assert resp.status_code == 200
    assert resp.json["ok"] is True


def test_get_resource_tree_graph(auth_client, monkeypatch):
    monkeypatch.setattr(
        "dashboard.api.get_provider",
        lambda name: type("P", (), {
            "discover_resources": lambda *a, **k: [],
            "regions": lambda *a, **k: ["cn-north-1"],
            "is_enabled": lambda *a, **k: True,
        })()
    )
    resp = auth_client.get("/api/dashboard/resource-tree/graph?provider=aws")
    assert resp.status_code == 200
    data = resp.json
    assert data["ok"] is True
    assert "nodes" in data
    assert "edges" in data


def test_post_and_delete_relation(auth_client):
    resp = auth_client.post(
        "/api/dashboard/resource-tree/relations",
        json={"source_id": "a", "target_id": "b", "relation_type": "depends_on"}
    )
    assert resp.status_code == 200
    rid = resp.json["id"]
    resp = auth_client.delete(f"/api/dashboard/resource-tree/relations/{rid}")
    assert resp.status_code == 200
    assert resp.json["ok"] is True
