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
    # Use a temp DB for tree store so tests don't pollute the workspace
    tree_db_path = str(tmp_path / "resource_tree.db")
    monkeypatch.setattr("dashboard.api._DEFAULT_TREE_DB", tree_db_path)
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
            "resource_types": lambda *a, **k: ["ec2"],
            "is_enabled": lambda *a, **k: True,
        })()
    )
    resp = auth_client.get("/api/dashboard/resource-tree/graph?provider=aws")
    assert resp.status_code == 200
    data = resp.json
    assert data["ok"] is True
    assert "nodes" in data
    assert "edges" in data


def test_get_resource_tree_graph_invalid_provider(auth_client, monkeypatch):
    def _raise(name):
        raise ValueError(f"Unknown provider: {name}")
    monkeypatch.setattr("dashboard.api.get_provider", _raise)
    resp = auth_client.get("/api/dashboard/resource-tree/graph?provider=invalid")
    assert resp.status_code == 400
    assert resp.json["ok"] is False
    assert "Unknown provider" in resp.json["error"]


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


def test_delete_auto_scan_relation_returns_403(auth_client, monkeypatch):
    # Seed an auto_scan relation directly via the store
    from dashboard.api import _get_tree_store
    store = _get_tree_store()
    rid = store.add_relation("s", "t", "contains", "auto_scan", provider="aws")
    resp = auth_client.delete(f"/api/dashboard/resource-tree/relations/{rid}")
    assert resp.status_code == 403
    assert resp.json["ok"] is False


def test_delete_nonexistent_relation_returns_404(auth_client):
    resp = auth_client.delete("/api/dashboard/resource-tree/relations/nonexistent-id")
    assert resp.status_code == 404
    assert resp.json["ok"] is False


def test_put_positions(auth_client):
    resp = auth_client.put(
        "/api/dashboard/resource-tree/positions",
        json={"positions": {"node-a": {"x": 10, "y": 20}, "node-b": {"x": 30, "y": 40}}}
    )
    assert resp.status_code == 200
    assert resp.json["ok"] is True

    # Verify persistence
    from dashboard.api import _get_tree_store
    store = _get_tree_store()
    positions = store.get_positions()
    assert positions.get("node-a") == {"x": 10, "y": 20}
    assert positions.get("node-b") == {"x": 30, "y": 40}


def test_post_scan_rejects_non_aws(auth_client):
    resp = auth_client.post(
        "/api/dashboard/resource-tree/scan",
        json={"provider": "tencent"}
    )
    assert resp.status_code == 400
    assert resp.json["ok"] is False
    assert "Auto-scan only supported for AWS" in resp.json["error"]


def test_get_scan_status(auth_client, monkeypatch):
    # Directly inject a job so we don't need to spawn a thread
    monkeypatch.setattr("dashboard.api._scan_jobs", {
        "test-job-123": {"status": "running", "count": 0, "error": None}
    })
    resp = auth_client.get("/api/dashboard/resource-tree/scan/test-job-123")
    assert resp.status_code == 200
    assert resp.json["ok"] is True
    assert resp.json["status"] == "running"


def test_get_scan_status_unknown_job(auth_client):
    resp = auth_client.get("/api/dashboard/resource-tree/scan/does-not-exist")
    assert resp.status_code == 200
    assert resp.json["ok"] is True
    assert resp.json["status"] == "unknown"
