import json
import logging

import pytest

from multi_profile.external_validation import run_validation_pipeline
from multi_profile.health import ProfileHealthMonitor
from multi_profile.models import AppConfig, ProfileConfig, create_snapshot
from multi_profile.operational_settings import OperationalSettings
from multi_profile.sts import StsResult, mask_account_id


SECRET_VALUES = [
    "s3cret-feishu-value",
    "AKIAIOSFODNN7EXAMPLE",
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
]


def make_snapshot():
    apps = {
        "ops-bot": AppConfig(
            app_key="ops-bot",
            app_id_env="FEISHU_OPS_APP_ID",
            app_secret_env="FEISHU_OPS_APP_SECRET",
            default_profile="prod-cn",
        )
    }
    profiles = {
        "prod-cn": ProfileConfig(
            profile_id="prod-cn",
            aws_profile="production",
            expected_account_id="123456789012",
            working_dir="/srv/kiro-devops",
        )
    }
    return create_snapshot(1, apps, profiles, ())


def all_log_text(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def test_validation_failure_logs_contain_no_secret_or_full_account(caplog, tmp_path):
    yaml_text = """
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
""".format(working_dir=tmp_path)
    environ = {
        "FEISHU_OPS_APP_ID": "cli_test",
        "FEISHU_OPS_APP_SECRET": SECRET_VALUES[0],
    }

    with caplog.at_level(logging.DEBUG):
        report = run_validation_pipeline(
            yaml_text,
            environ=environ,
            kiro_agents_dir=tmp_path / "no-agents",
            aws_config_dir=tmp_path / "no-aws",
            model_lister=lambda: [],
            sts_runner=lambda profile, **kw: StsResult(
                True, "999999999999", None, "ok",
            ),
        )

    assert report.ok is False
    combined = all_log_text(caplog) + json.dumps(
        [s.__dict__ for s in report.stages],
    )
    for secret in SECRET_VALUES:
        assert secret not in combined
    # Account ID 只以遮罩形式出現
    assert "999999999999" not in combined
    if "********" in combined:
        assert "********9999" in combined


def test_health_monitor_logs_contain_no_full_account(caplog):
    snapshot = make_snapshot()

    def sts_runner(profile, **kw):
        return StsResult(True, "999999999999", None, "ok")

    monitor = ProfileHealthMonitor(
        lambda: snapshot,
        settings=OperationalSettings(),
        sts_runner=sts_runner,
    )
    with caplog.at_level(logging.DEBUG):
        monitor.check_all_now()

    assert "999999999999" not in all_log_text(caplog)
    assert monitor.health("prod-cn").account_id_masked == "********9999"


def test_mask_account_id_format_is_stable():
    assert mask_account_id("123456789012") == "********9012"
    assert len(mask_account_id("123456789012")) == 12


def test_health_monitor_never_logs_child_environment(caplog, monkeypatch):
    """規格 §16：不得輸出完整子程序環境。"""
    snapshot = make_snapshot()
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", SECRET_VALUES[2])

    captured = {}

    def sts_runner(profile, **kw):
        return StsResult(True, "123456789012", None, "ok")

    monitor = ProfileHealthMonitor(
        lambda: snapshot, settings=OperationalSettings(), sts_runner=sts_runner,
    )
    with caplog.at_level(logging.DEBUG):
        monitor.check_all_now()

    for secret in SECRET_VALUES:
        assert secret not in all_log_text(caplog)
