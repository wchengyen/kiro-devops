from pathlib import Path

import pytest

from multi_profile.config_loader import ConfigError, load_config


VALID_YAML = """
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
routes:
  - app: ops-bot
    chat_id: oc_prod
    profile: prod-cn
"""


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "multi_profile_config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def valid_env() -> dict[str, str]:
    return {
        "FEISHU_OPS_APP_ID": "cli_test",
        "FEISHU_OPS_APP_SECRET": "secret_test",
    }


def test_load_config_applies_defaults(tmp_path):
    path = write_config(tmp_path, VALID_YAML.format(working_dir=tmp_path))

    snapshot = load_config(path, environ=valid_env(), generation=7)

    assert snapshot.version == 1
    assert snapshot.generation == 7
    assert snapshot.apps["ops-bot"].enabled is True
    assert snapshot.profiles["prod-cn"].aws_region is None
    assert snapshot.profiles["prod-cn"].alert_agent == "ec2-alert-analyzer"
    assert snapshot.profiles["prod-cn"].sync_timeout == 120
    assert snapshot.routes[0].poll_alerts is False


def test_explicit_empty_environment_does_not_use_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("FEISHU_OPS_APP_ID", "host_app")
    monkeypatch.setenv("FEISHU_OPS_APP_SECRET", "host_secret")
    path = write_config(tmp_path, VALID_YAML.format(working_dir=tmp_path))

    with pytest.raises(ConfigError, match="missing env FEISHU_OPS_APP_ID"):
        load_config(path, environ={})


def test_load_config_rejects_unknown_fields(tmp_path):
    text = VALID_YAML.format(working_dir=tmp_path).replace(
        "    default_profile: prod-cn",
        "    default_profile: prod-cn\n    app_secret_value: forbidden",
    )
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError, match="apps.ops-bot.*unknown field.*app_secret_value"):
        load_config(path, environ=valid_env())


def test_load_config_reports_yaml_errors(tmp_path):
    path = write_config(tmp_path, "version: [")

    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path, environ={})


def test_rejects_boolean_version(tmp_path):
    text = VALID_YAML.format(working_dir=tmp_path).replace("version: 1", "version: true")
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError, match="config.version must be integer 1"):
        load_config(path, environ=valid_env())


@pytest.mark.parametrize(
    ("text_transform", "message"),
    [
        (lambda text: text.replace("  ops-bot:", "  123:", 1), "apps keys must be strings"),
        (lambda text: text.replace("  prod-cn:", "  123:", 1), "profiles keys must be strings"),
        (
            lambda text: text.replace(
                "    default_profile: prod-cn",
                "    default_profile: prod-cn\n    123: invalid",
            ),
            "apps.123 keys must be strings|apps.ops-bot keys must be strings",
        ),
        (lambda text: text + "\n123: invalid\n", "config keys must be strings"),
    ],
)
def test_rejects_non_string_schema_keys(tmp_path, text_transform, message):
    text = text_transform(VALID_YAML.format(working_dir=tmp_path))
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError, match=message):
        load_config(path, environ=valid_env())


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("expected_account_id: \"123456789012\"", "expected_account_id: \"123\"", "expected_account_id"),
        ("sync_timeout: 120", "sync_timeout: 9", "sync_timeout"),
        ("async_timeout: 1800", "async_timeout: 60", "async_timeout"),
        ("alert_timeout: 300", "alert_timeout: 3601", "alert_timeout"),
    ],
)
def test_rejects_invalid_account_and_timeout_values(tmp_path, old, new, message):
    base = VALID_YAML.format(working_dir=tmp_path).replace(
        "    working_dir:",
        "    sync_timeout: 120\n    async_timeout: 1800\n    alert_timeout: 300\n    working_dir:",
    )
    path = write_config(tmp_path, base.replace(old, new))

    with pytest.raises(ConfigError, match=message):
        load_config(path, environ=valid_env())


def test_rejects_missing_working_directory(tmp_path):
    path = write_config(
        tmp_path,
        VALID_YAML.format(working_dir=tmp_path / "missing"),
    )

    with pytest.raises(ConfigError, match="working_dir.*existing directory"):
        load_config(path, environ=valid_env())


def test_rejects_duplicate_routes(tmp_path):
    text = VALID_YAML.format(working_dir=tmp_path) + """
  - app: ops-bot
    chat_id: oc_prod
    profile: prod-cn
"""
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError, match="duplicate route.*ops-bot.*oc_prod"):
        load_config(path, environ=valid_env())


def test_rejects_missing_route_profile(tmp_path):
    text = VALID_YAML.format(working_dir=tmp_path).replace(
        "    profile: prod-cn",
        "    profile: missing",
    )
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError, match="routes.*missing profile"):
        load_config(path, environ=valid_env())


def test_rejects_disabled_default_profile(tmp_path):
    text = VALID_YAML.format(working_dir=tmp_path).replace(
        "  prod-cn:\n",
        "  prod-cn:\n    enabled: false\n",
    )
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError, match="default_profile.*disabled"):
        load_config(path, environ=valid_env())


def test_disabled_app_still_requires_enabled_default_profile(tmp_path):
    text = VALID_YAML.format(working_dir=tmp_path)
    text = text.replace(
        "  ops-bot:\n",
        "  ops-bot:\n    enabled: false\n",
    ).replace(
        "  prod-cn:\n",
        "  prod-cn:\n    enabled: false\n",
    )
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError, match="default_profile.*disabled"):
        load_config(path, environ=valid_env())
