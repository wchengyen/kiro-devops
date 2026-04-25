#!/usr/bin/env python3
"""Dashboard ConfigStore tests."""

import json
import os
import tempfile

import pytest

from dashboard.config_store import ConfigStore


CORE_KEYS = [
    "KIRO_AGENT",
    "ALERT_NOTIFY_USER_ID",
    "ALERT_AUTO_ANALYZE_SEVERITY",
    "WEBHOOK_TOKEN",
    "WEBHOOK_PORT",
    "WEBHOOK_HOST",
    "ENABLE_MEMORY",
    "GROUP_AT_ONLY",
]


@pytest.fixture
def temp_env_file():
    """Provide a temporary .env file path."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("# This is a comment\n")
        f.write("KIRO_AGENT=my-agent\n")
        f.write("ALERT_NOTIFY_USER_ID=ou_123\n")
        f.write("\n")
        f.write("WEBHOOK_TOKEN=secret-token\n")
        f.write("WEBHOOK_PORT=8080\n")
        f.write("# Another comment\n")
        f.write("ENABLE_MEMORY=true\n")
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def temp_mappings_file():
    """Provide a temporary dashboard_config.json path."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        path = f.name
    yield path
    os.unlink(path)


def test_read_core_config(temp_env_file):
    store = ConfigStore(env_path=temp_env_file, mappings_path="/nonexistent.json")
    config = store.read_core_config()

    assert isinstance(config, dict)
    assert config["KIRO_AGENT"] == "my-agent"
    assert config["ALERT_NOTIFY_USER_ID"] == "ou_123"
    assert config["WEBHOOK_TOKEN"] == "secret-token"
    assert config["WEBHOOK_PORT"] == "8080"
    assert config["ENABLE_MEMORY"] == "true"
    # Missing keys should return empty string
    assert config["GROUP_AT_ONLY"] == ""
    assert config["WEBHOOK_HOST"] == ""
    assert config["ALERT_AUTO_ANALYZE_SEVERITY"] == ""


def test_write_core_config(temp_env_file):
    store = ConfigStore(env_path=temp_env_file, mappings_path="/nonexistent.json")
    store.write_core_config({
        "KIRO_AGENT": "new-agent",
        "WEBHOOK_PORT": "9090",
        "GROUP_AT_ONLY": "false",
    })

    # Re-read from disk to verify
    with open(temp_env_file, "r") as f:
        content = f.read()

    # Existing comments should be preserved
    assert "# This is a comment" in content
    assert "# Another comment" in content

    # Updated values
    assert "KIRO_AGENT=new-agent" in content
    assert "WEBHOOK_PORT=9090" in content
    assert "GROUP_AT_ONLY=false" in content

    # Values not in updates should remain unchanged
    assert "ALERT_NOTIFY_USER_ID=ou_123" in content
    assert "WEBHOOK_TOKEN=secret-token" in content
    assert "ENABLE_MEMORY=true" in content

    # Verify via read_core_config as well
    config = store.read_core_config()
    assert config["KIRO_AGENT"] == "new-agent"
    assert config["WEBHOOK_PORT"] == "9090"
    assert config["GROUP_AT_ONLY"] == "false"


def test_mappings_roundtrip(temp_mappings_file):
    store = ConfigStore(env_path="/nonexistent.env", mappings_path=temp_mappings_file)
    mappings = [
        {"alert_keyword": "cpu", "agent": "infra-agent"},
        {"alert_keyword": "disk", "agent": "storage-agent"},
    ]
    store.write_mappings(mappings)

    read_back = store.read_mappings()
    assert read_back == mappings


def test_read_mappings_missing_file():
    store = ConfigStore(env_path="/nonexistent.env", mappings_path="/nonexistent.json")
    assert store.read_mappings() == []


def test_read_mappings_malformed_json(temp_mappings_file):
    with open(temp_mappings_file, "w") as f:
        f.write("not json at all")
    store = ConfigStore(env_path="/nonexistent.env", mappings_path=temp_mappings_file)
    assert store.read_mappings() == []


def test_pinned_resources_roundtrip(temp_mappings_file):
    store = ConfigStore(env_path="/nonexistent.env", mappings_path=temp_mappings_file)
    pins = ["ec2:i-123", "rds:my-db"]
    store.write_pinned_resources(pins)

    read_back = store.read_pinned_resources()
    assert read_back == pins


def test_read_pinned_resources_missing_file():
    store = ConfigStore(env_path="/nonexistent.env", mappings_path="/nonexistent.json")
    assert store.read_pinned_resources() == []


def test_read_pinned_resources_preserves_other_keys(temp_mappings_file):
    store = ConfigStore(env_path="/nonexistent.env", mappings_path=temp_mappings_file)
    store.write_mappings([{"alert_keyword": "cpu", "agent": "infra-agent"}])
    store.write_pinned_resources(["ec2:i-123"])

    assert store.read_mappings() == [{"alert_keyword": "cpu", "agent": "infra-agent"}]
    assert store.read_pinned_resources() == ["ec2:i-123"]
