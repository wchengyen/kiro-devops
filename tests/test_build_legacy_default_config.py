import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_legacy_default_config.py"

ENV_TEXT = """
FEISHU_APP_ID=cli_current
FEISHU_APP_SECRET=current_secret
FEISHU_POLL_CHAT_IDS=oc_alpha, oc_beta ,oc_gamma
KIRO_AGENT=my-dev-bot
DEFAULT_MODEL=claude-sonnet
BACKGROUND_MODEL=claude-haiku
KIRO_SYNC_TIMEOUT=180
KIRO_ASYNC_TIMEOUT=2400
ALERT_ANALYZE_TIMEOUT=240
AWS_PROFILE=production
AWS_REGION=cn-northwest-1
"""


def run_script(*args, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_generates_legacy_default_draft(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_TEXT, encoding="utf-8")

    result = run_script(
        "--env-file", str(env_file),
        "--account-id", "123456789012",
        "--working-dir", str(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    draft = yaml.safe_load(result.stdout)
    assert draft["version"] == 1

    app = draft["apps"]["legacy-bot"]
    assert app["app_id_env"] == "FEISHU_APP_ID"
    assert app["app_secret_env"] == "FEISHU_APP_SECRET"
    assert app["default_profile"] == "legacy-default"
    assert "current_secret" not in result.stdout  # 只引用變數名，不輸出 Secret 值

    profile = draft["profiles"]["legacy-default"]
    assert profile["aws_profile"] == "production"
    assert profile["aws_region"] == "cn-northwest-1"
    assert profile["expected_account_id"] == "123456789012"
    assert profile["kiro_agent"] == "my-dev-bot"
    assert profile["model"] == "claude-sonnet"
    assert profile["alert_model"] == "claude-haiku"
    assert profile["sync_timeout"] == 180
    assert profile["async_timeout"] == 2400
    assert profile["alert_timeout"] == 240

    routes = {(r["app"], r["chat_id"]): r for r in draft["routes"]}
    assert set(routes) == {
        ("legacy-bot", "oc_alpha"),
        ("legacy-bot", "oc_beta"),
        ("legacy-bot", "oc_gamma"),
    }
    assert all(route["poll_alerts"] is True for route in routes.values())
    assert all(route["profile"] == "legacy-default" for route in routes.values())


def test_defaults_when_optional_env_missing(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FEISHU_APP_ID=cli_current\nFEISHU_APP_SECRET=s\n", encoding="utf-8",
    )

    result = run_script(
        "--env-file", str(env_file),
        "--account-id", "123456789012",
        "--working-dir", str(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    draft = yaml.safe_load(result.stdout)
    profile = draft["profiles"]["legacy-default"]
    assert profile["aws_profile"] == "default"
    assert profile.get("aws_region") is None
    assert profile.get("kiro_agent") is None
    assert profile.get("model") is None
    assert profile.get("alert_model") is None
    assert profile["sync_timeout"] == 120
    assert profile["async_timeout"] == 1800
    assert profile["alert_timeout"] == 300
    assert draft["routes"] == []


def test_output_is_loadable_by_plan1_loader(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_TEXT, encoding="utf-8")

    generated = run_script(
        "--env-file", str(env_file),
        "--account-id", "123456789012",
        "--working-dir", str(tmp_path),
    )
    assert generated.returncode == 0, generated.stderr
    config_path = tmp_path / "multi_profile_config.yaml"
    config_path.write_text(generated.stdout, encoding="utf-8")

    from multi_profile import load_config

    snapshot = load_config(
        config_path,
        environ={"FEISHU_APP_ID": "cli_current", "FEISHU_APP_SECRET": "s"},
    )
    assert snapshot.profiles["legacy-default"].aws_profile == "production"
    assert len(snapshot.routes) == 3


def test_refuses_to_overwrite_existing_config_without_force(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_TEXT, encoding="utf-8")
    existing = tmp_path / "multi_profile_config.yaml"
    existing.write_text("version: 1\n", encoding="utf-8")

    result = run_script(
        "--env-file", str(env_file),
        "--account-id", "123456789012",
        "--working-dir", str(tmp_path),
        "--output", str(existing),
    )

    assert result.returncode != 0
    assert "--force" in result.stderr
    assert existing.read_text(encoding="utf-8") == "version: 1\n"


def test_invalid_account_id_rejected(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_TEXT, encoding="utf-8")

    result = run_script(
        "--env-file", str(env_file),
        "--account-id", "123",
        "--working-dir", str(tmp_path),
    )

    assert result.returncode != 0
    assert "expected_account_id" in result.stderr or "12" in result.stderr
