from pathlib import Path

import pytest

from multi_profile.config_loader import ConfigError
from multi_profile.registry import ConfigRegistry


CONFIG = """
version: 1
apps:
  ops-bot:
    app_id_env: FEISHU_OPS_APP_ID
    app_secret_env: FEISHU_OPS_APP_SECRET
    default_profile: prod-cn
profiles:
  prod-cn:
    aws_profile: production
    expected_account_id: "123456789012"
    working_dir: {working_dir}
routes: []
"""


@pytest.fixture
def environ():
    return {
        "FEISHU_OPS_APP_ID": "cli_test",
        "FEISHU_OPS_APP_SECRET": "secret_test",
    }


def test_registry_loads_generation_one(tmp_path, environ):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.format(working_dir=tmp_path), encoding="utf-8")
    registry = ConfigRegistry(path, environ=environ)

    snapshot = registry.load_initial()

    assert snapshot.generation == 1
    assert registry.snapshot() is snapshot


def test_reload_publishes_next_generation(tmp_path, environ):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.format(working_dir=tmp_path), encoding="utf-8")
    registry = ConfigRegistry(path, environ=environ)
    first = registry.load_initial()

    second = registry.reload()

    assert first.generation == 1
    assert second.generation == 2
    assert registry.snapshot() is second


def test_failed_reload_keeps_previous_snapshot(tmp_path, environ):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.format(working_dir=tmp_path), encoding="utf-8")
    registry = ConfigRegistry(path, environ=environ)
    first = registry.load_initial()
    path.write_text("version: [", encoding="utf-8")

    with pytest.raises(ConfigError):
        registry.reload()

    assert registry.snapshot() is first
    assert registry.snapshot().generation == 1


def test_snapshot_requires_successful_initial_load(tmp_path, environ):
    registry = ConfigRegistry(tmp_path / "missing.yaml", environ=environ)

    with pytest.raises(RuntimeError, match="not loaded"):
        registry.snapshot()
