import pytest

from multi_profile.external_validation import StageResult, ValidationReport
from multi_profile.health import ProfileHealthMonitor
from multi_profile.operational_settings import OperationalSettings
from multi_profile.publisher import (
    ChangeSummary,
    ConfigPublisher,
    PublishError,
    classify_changes,
)
from multi_profile.registry import ConfigRegistry
from multi_profile.revisions import RevisionStore, config_checksum


BASE_YAML = """
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

ENVIRON = {
    "FEISHU_OPS_APP_ID": "cli_test",
    "FEISHU_OPS_APP_SECRET": "secret_test",
    "FEISHU_EU_APP_ID": "cli_eu",
    "FEISHU_EU_APP_SECRET": "secret_eu",
}


def ok_report(snapshot):
    return ValidationReport(
        True,
        (StageResult("yaml_schema", True, "ok"), StageResult("sts_identity", True, "ok")),
        snapshot,
    )


@pytest.fixture
def stack(tmp_path, monkeypatch):
    config_path = tmp_path / "multi_profile_config.yaml"
    config_path.write_text(BASE_YAML.format(working_dir=tmp_path), encoding="utf-8")
    registry = ConfigRegistry(config_path, environ=ENVIRON)
    registry.load_initial()
    store = RevisionStore(tmp_path / "revs")
    monitor = ProfileHealthMonitor(
        registry.snapshot, settings=OperationalSettings(),
        sts_runner=lambda *a, **kw: None,
    )
    publisher = ConfigPublisher(
        registry=registry,
        revision_store=store,
        health_monitor=monitor,
        validator=lambda text: ok_report(None),
    )
    return publisher, registry, store, monitor, config_path


def test_publish_switches_generation_and_records_revision(stack, tmp_path):
    publisher, registry, store, monitor, config_path = stack
    new_yaml = BASE_YAML.format(working_dir=tmp_path).replace(
        "routes: []",
        "routes:\n  - app: ops-bot\n    chat_id: oc_prod\n    profile: prod-cn\n",
    )

    result = publisher.publish(new_yaml)

    assert result.generation == 2
    assert result.checksum == config_checksum(new_yaml)
    assert registry.snapshot().generation == 2
    assert config_path.read_text(encoding="utf-8") == new_yaml
    assert len(store.list()) == 1
    assert store.list()[0].source == "publish"
    assert (store.directory / "last-known-good.yaml").read_text() == new_yaml
    assert result.change_summary.hot_reloadable
    assert result.change_summary.pending_restart == ()


def test_publish_reruns_validation_and_refuses_invalid_draft(stack):
    publisher, registry, store, _, config_path = stack
    before = config_path.read_text()
    publisher._validator = lambda text: ValidationReport(
        False, (StageResult("sts_identity", False, "timeout"),), None,
    )

    with pytest.raises(PublishError, match="sts_identity"):
        publisher.publish("version: 1\n")

    assert config_path.read_text() == before  # 檔案未被更動
    assert registry.snapshot().generation == 1
    assert store.list() == []


def test_snapshot_failure_restores_previous_revision_atomically(stack, tmp_path):
    """規格 §13.4：os.replace 成功但 snapshot 建立失敗時，恢復上一 revision。"""
    publisher, registry, store, _, config_path = stack
    before = config_path.read_text()

    original_reload = registry.reload

    def failing_reload():
        if config_path.read_text() != before:
            raise RuntimeError("snapshot build failed")
        return original_reload()

    registry.reload = failing_reload
    new_yaml = before + "# touched\n"

    with pytest.raises(PublishError, match="restored previous revision"):
        publisher.publish(new_yaml)

    registry.reload = original_reload
    assert config_path.read_text() == before
    assert registry.snapshot().generation == 1
    assert store.list() == []  # 失敗發布不留 revision


def test_app_credential_env_change_is_pending_restart(stack, tmp_path):
    publisher, registry, store, _, _ = stack
    new_yaml = BASE_YAML.format(working_dir=tmp_path).replace(
        "app_secret_env: FEISHU_OPS_APP_SECRET",
        "app_secret_env: FEISHU_EU_APP_SECRET",
    )

    result = publisher.publish(new_yaml)

    assert result.generation == 2  # 允許保存
    assert result.change_summary.pending_restart == (
        "app ops-bot credential env changed",
    )


def test_app_add_and_remove_are_pending_restart(tmp_path):
    from multi_profile.config_loader import load_config

    old = load_config(
        _write(tmp_path, "old.yaml", BASE_YAML.format(working_dir=tmp_path)),
        environ=ENVIRON, generation=1,
    )
    new_yaml = BASE_YAML.format(working_dir=tmp_path).replace(
        "profiles:",
        "  eu-bot:\n"
        "    app_id_env: FEISHU_EU_APP_ID\n"
        "    app_secret_env: FEISHU_EU_APP_SECRET\n"
        "    default_profile: prod-cn\n"
        "profiles:",
    )
    new = load_config(
        _write(tmp_path, "new.yaml", new_yaml), environ=ENVIRON, generation=2,
    )

    summary = classify_changes(old, new)
    assert "app eu-bot added" in summary.pending_restart

    summary_removed = classify_changes(new, old)
    assert "app eu-bot removed" in summary_removed.pending_restart


def test_route_profile_and_default_profile_changes_are_hot_reloadable(tmp_path):
    from multi_profile.config_loader import load_config

    old = load_config(
        _write(tmp_path, "old.yaml", BASE_YAML.format(working_dir=tmp_path)),
        environ=ENVIRON, generation=1,
    )
    new_yaml = BASE_YAML.format(working_dir=tmp_path).replace(
        "routes: []",
        "routes:\n  - app: ops-bot\n    chat_id: oc_a\n    profile: prod-cn\n"
        "    poll_alerts: true\n",
    ).replace("sync_timeout", "sync_timeout")  # profile 欄位另行覆蓋
    new_yaml = new_yaml.replace(
        "    working_dir:",
        "    sync_timeout: 240\n    working_dir:",
    )
    new = load_config(
        _write(tmp_path, "new.yaml", new_yaml), environ=ENVIRON, generation=2,
    )

    summary = classify_changes(old, new)

    assert summary.pending_restart == ()
    assert "routes changed" in summary.hot_reloadable
    assert "profile prod-cn execution fields changed" in summary.hot_reloadable


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_rollback_revalidates_and_publishes_as_new_revision(stack, tmp_path):
    publisher, registry, store, _, config_path = stack
    first_yaml = config_path.read_text()
    second_yaml = first_yaml.replace(
        "routes: []",
        "routes:\n  - app: ops-bot\n    chat_id: oc_prod\n    profile: prod-cn\n",
    )
    publisher.publish(second_yaml)
    # publish 只保存新內容為 revision；要回滾到發布前內容需另行保存
    # （與 Dashboard API 層測試的語意一致）
    store.save(first_yaml, generation=1, source="publish", validation_summary="ok")
    target = next(
        r.revision_id for r in store.list() if r.checksum == config_checksum(first_yaml)
    )

    calls = []
    original = publisher._validator
    publisher._validator = lambda text: (calls.append(text), original(text))[1]

    result = publisher.rollback(target)

    assert calls == [first_yaml]  # 回滾內容經過完整重新驗證（含 STS）
    assert result.generation == 3
    assert registry.snapshot().generation == 3
    assert config_path.read_text() == first_yaml
    assert store.list()[-1].source == "rollback"


def test_rollback_validation_failure_keeps_current_snapshot(stack, tmp_path):
    publisher, registry, store, _, config_path = stack
    first_yaml = config_path.read_text()
    publisher.publish(first_yaml + "# v2\n")
    target = store.list()[0].revision_id
    publisher._validator = lambda text: ValidationReport(
        False, (StageResult("expected_account", False, "mismatch"),), None,
    )

    with pytest.raises(PublishError):
        publisher.rollback(target)

    assert registry.snapshot().generation == 2
    assert config_path.read_text() == first_yaml + "# v2\n"


def test_last_results_are_tracked_for_observability(stack, tmp_path):
    publisher, *_ = stack
    publisher.publish(BASE_YAML.format(working_dir=tmp_path) + "# v2\n")

    last = publisher.last_result
    assert last.ok is True
    assert last.action == "publish"
    assert last.error is None
