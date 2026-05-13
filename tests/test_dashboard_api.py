#!/usr/bin/env python3
"""Tests for dashboard API routes."""

import subprocess

import pytest
from flask import Flask

from dashboard import dashboard_bp, _sessions
import dashboard.api  # noqa: F401 — registers routes via side effect
from dashboard.config_store import ConfigStore


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
def auth_client(client):
    """Log in and return an authenticated test client."""
    resp = client.post("/api/dashboard/auth", json={"token": "test-secret-token"})
    assert resp.status_code == 200
    return client


def test_get_agents(auth_client):
    resp = auth_client.get("/api/dashboard/agents")
    assert resp.status_code == 200
    assert "agents" in resp.json


def test_get_skills(auth_client):
    resp = auth_client.get("/api/dashboard/skills")
    assert resp.status_code == 200
    assert "skills" in resp.json


def test_post_skill_creates_skill(auth_client, monkeypatch, tmp_path):
    skills_dir = tmp_path / ".kiro" / "skills"
    monkeypatch.setattr("dashboard.api.create_skill", lambda name, desc: True)

    resp = auth_client.post("/api/dashboard/skills", json={"name": "my-skill", "description": "A skill"})
    assert resp.status_code == 200
    assert resp.json == {"ok": True}


def test_post_skill_rejects_invalid_name(auth_client):
    resp = auth_client.post("/api/dashboard/skills", json={"name": "", "description": ""})
    assert resp.status_code == 400
    assert resp.json["ok"] is False


def test_delete_skill_removes_skill(auth_client, monkeypatch):
    monkeypatch.setattr("dashboard.api.delete_skill", lambda name: True)

    resp = auth_client.delete("/api/dashboard/skills/my-skill")
    assert resp.status_code == 200
    assert resp.json == {"ok": True}


def test_delete_skill_not_found(auth_client, monkeypatch):
    monkeypatch.setattr("dashboard.api.delete_skill", lambda name: False)

    resp = auth_client.delete("/api/dashboard/skills/missing")
    assert resp.status_code == 404
    assert resp.json["ok"] is False


def test_get_agent_skills(auth_client, monkeypatch):
    monkeypatch.setattr(
        "dashboard.api.get_agent_skills",
        lambda name: [{"name": "skill-a", "resource": "skill://.kiro/skills/skill-a/SKILL.md"}],
    )

    resp = auth_client.get("/api/dashboard/agents/my-agent/skills")
    assert resp.status_code == 200
    assert resp.json["ok"] is True
    assert resp.json["skills"][0]["name"] == "skill-a"


def test_post_agent_skill(auth_client, monkeypatch):
    monkeypatch.setattr("dashboard.api.add_skill_to_agent", lambda agent, skill: True)

    resp = auth_client.post("/api/dashboard/agents/my-agent/skills", json={"skill_name": "new-skill"})
    assert resp.status_code == 200
    assert resp.json == {"ok": True}


def test_post_agent_skill_missing_agent(auth_client, monkeypatch):
    monkeypatch.setattr("dashboard.api.add_skill_to_agent", lambda agent, skill: False)

    resp = auth_client.post("/api/dashboard/agents/missing/skills", json={"skill_name": "new-skill"})
    assert resp.status_code == 404
    assert resp.json["ok"] is False


def test_delete_agent_skill(auth_client, monkeypatch):
    monkeypatch.setattr("dashboard.api.remove_skill_from_agent", lambda agent, skill: True)

    resp = auth_client.delete("/api/dashboard/agents/my-agent/skills/old-skill")
    assert resp.status_code == 200
    assert resp.json == {"ok": True}


def test_get_config(auth_client, monkeypatch, tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("KIRO_AGENT=my-agent\nWEBHOOK_TOKEN=secret123\n")
    monkeypatch.setenv("ENV_PATH", str(env_file))

    resp = auth_client.get("/api/dashboard/config")
    assert resp.status_code == 200
    assert "config" in resp.json
    assert resp.json["config"]["WEBHOOK_TOKEN"] == "***"


def test_post_mappings(auth_client, monkeypatch, tmp_path):
    mappings_file = tmp_path / "dashboard_config.json"

    # Patch ConfigStore.__init__ in api.py so the default mappings_path points to tmp
    original_init = ConfigStore.__init__

    def patched_init(self, env_path=".env", mappings_path="dashboard_config.json"):
        original_init(self, env_path=env_path, mappings_path=str(mappings_file))

    monkeypatch.setattr("dashboard.api.ConfigStore.__init__", patched_init)

    payload = {"mappings": [{"alert_keyword": "cpu", "agent": "infra-agent"}]}
    resp = auth_client.post("/api/dashboard/mappings", json=payload)
    assert resp.status_code == 200
    assert resp.json == {"ok": True}


def test_get_models(auth_client, monkeypatch):
    """Test /models returns structure even if kiro-cli is mocked."""
    def mock_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = '{"models": [{"model_id": "test-model"}], "default_model": "test-model"}'
        return R()

    monkeypatch.setattr(subprocess, "run", mock_run)
    resp = auth_client.get("/api/dashboard/models")
    assert resp.status_code == 200
    data = resp.json
    assert data["models"] == [{"model_id": "test-model"}]
    assert data["default_model"] == "test-model"


def test_get_models_fallback_on_error(auth_client, monkeypatch):
    """Test /models returns empty list when kiro-cli fails."""
    def mock_run(*args, **kwargs):
        class R:
            returncode = 1
            stdout = ""
        return R()

    monkeypatch.setattr(subprocess, "run", mock_run)
    resp = auth_client.get("/api/dashboard/models")
    assert resp.status_code == 500
    data = resp.json
    assert data["models"] == []
    assert data["default_model"] is None
    assert data["error"] == "kiro-cli failed"
