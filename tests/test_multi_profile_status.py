from multi_profile.health import ProfileHealthMonitor
from multi_profile.operational_settings import OperationalSettings
from multi_profile.publisher import LastActionResult
from multi_profile.registry import ConfigRegistry
from multi_profile.revisions import RevisionStore, config_checksum
from multi_profile.status import build_multi_profile_status
from multi_profile.sts import StsResult
from multi_profile.task_registry import TaskRegistry


YAML = """
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


class FakeAppManager:
    def app_statuses(self):
        return {"ops-bot": "connected", "eu-bot": "pending-restart"}


def make_stack(tmp_path):
    config_path = tmp_path / "config.yaml"
    text = YAML.format(working_dir=tmp_path)
    config_path.write_text(text, encoding="utf-8")
    registry = ConfigRegistry(config_path, environ={
        "FEISHU_OPS_APP_ID": "cli", "FEISHU_OPS_APP_SECRET": "sec",
    })
    registry.load_initial()
    monitor = ProfileHealthMonitor(
        registry.snapshot,
        settings=OperationalSettings(),
        sts_runner=lambda profile, **kw: StsResult(True, "123456789012", None, "ok"),
    )
    monitor.check_all_now()
    tasks = TaskRegistry()
    tasks.reserve("feishu/ops-bot/group/oc_a/user/ou_1", "prod-cn")
    return registry, monitor, tasks, text


def test_status_matches_spec_section_17(tmp_path):
    registry, monitor, tasks, text = make_stack(tmp_path)

    status = build_multi_profile_status(
        mode="multi-profile",
        registry=registry,
        config_text=text,
        health_monitor=monitor,
        app_manager=FakeAppManager(),
        task_registry=tasks,
        settings=OperationalSettings(),
        last_load=LastActionResult("load", True, "t0", None, "ok"),
        last_publish=LastActionResult("publish", True, "t1", None, "gen 2"),
        last_rollback=None,
    )

    assert status["mode"] == "multi-profile"
    assert status["generation"] == 1
    assert status["checksum"] == config_checksum(text)
    assert status["apps"] == {
        "ops-bot": "connected", "eu-bot": "pending-restart",
    }
    profile = status["profiles"]["prod-cn"]
    assert profile["state"] == "active"
    assert profile["account_id"] == "123456789012"  # Dashboard auth 後方可見完整值
    assert profile["account_id_masked"] == "********9012"
    assert profile["last_sts_at"] is not None
    assert status["tasks"] == {"total": 1, "by_profile": {"prod-cn": 1}}
    assert status["last_load"]["ok"] is True
    assert status["last_publish"]["action"] == "publish"
    assert status["last_rollback"] is None
    assert status["settings"]["health_check_interval_sec"] == 600


def test_legacy_mode_status_is_minimal(tmp_path):
    status = build_multi_profile_status(
        mode="legacy",
        registry=None,
        config_text=None,
        health_monitor=None,
        app_manager=None,
        task_registry=None,
        settings=OperationalSettings(),
        last_load=None,
        last_publish=None,
        last_rollback=None,
    )

    assert status["mode"] == "legacy"
    assert status["generation"] is None
    assert status["profiles"] == {}
    assert status["apps"] == {}
