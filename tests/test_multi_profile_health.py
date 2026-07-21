import pytest

from multi_profile.health import (
    ProfileHealthMonitor,
    ProfileUnavailable,
)
from multi_profile.models import ProfileConfig, create_snapshot, AppConfig
from multi_profile.sts import StsResult


def make_snapshot(*profiles, generation=1):
    apps = {
        "ops-bot": AppConfig(
            app_key="ops-bot",
            app_id_env="FEISHU_OPS_APP_ID",
            app_secret_env="FEISHU_OPS_APP_SECRET",
            default_profile=profiles[0].profile_id,
        )
    }
    return create_snapshot(generation, apps, {p.profile_id: p for p in profiles}, ())


def make_profile(profile_id="prod-cn", **changes):
    values = {
        "profile_id": profile_id,
        "aws_profile": "production",
        "expected_account_id": "123456789012",
        "working_dir": "/srv/kiro-devops",
    }
    values.update(changes)
    return ProfileConfig(**values)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_monitor(snapshot, clock, sts_results, settings_changes=None):
    from multi_profile.operational_settings import OperationalSettings

    settings = OperationalSettings(**(settings_changes or {}))

    def sts_runner(profile, **kwargs):
        return sts_results[profile.profile_id].pop(0)

    return ProfileHealthMonitor(
        lambda: snapshot,
        settings=settings,
        clock=clock,
        sts_runner=sts_runner,
    )


def ok(account="123456789012"):
    return StsResult(True, account, None, "ok")


def timeout():
    return StsResult(False, None, "timeout", "sts timeout after 10s")


def test_successful_sts_marks_profile_active_with_masked_account():
    clock = FakeClock()
    monitor = make_monitor(
        make_snapshot(make_profile()), clock, {"prod-cn": [ok()]},
    )

    monitor.check_all_now()
    health = monitor.health("prod-cn")

    assert health.state == "active"
    assert health.account_id_masked == "********9012"
    assert health.last_sts_at == 1000.0
    monitor.ensure_usable("prod-cn")  # 不拋出


def test_account_id_mismatch_blocks_immediately_without_grace():
    clock = FakeClock()
    monitor = make_monitor(
        make_snapshot(make_profile()), clock, {"prod-cn": [ok(account="999999999999")]},
    )

    monitor.check_all_now()
    health = monitor.health("prod-cn")

    assert health.state == "blocked"
    assert health.last_error == "account_mismatch"
    with pytest.raises(ProfileUnavailable, match="blocked"):
        monitor.ensure_usable("prod-cn")


def test_missing_aws_profile_blocks_immediately():
    clock = FakeClock()
    monitor = make_monitor(
        make_snapshot(make_profile()), clock,
        {"prod-cn": [StsResult(False, None, "profile_not_found", "could not be found")]},
    )

    monitor.check_all_now()

    assert monitor.health("prod-cn").state == "blocked"
    assert monitor.health("prod-cn").last_error == "profile_not_found"


def test_transient_failure_within_grace_is_degraded_then_recovers():
    clock = FakeClock()
    monitor = make_monitor(
        make_snapshot(make_profile()), clock, {"prod-cn": [ok(), timeout(), ok()]},
    )
    monitor.check_all_now()  # active

    clock.advance(60)
    monitor.check_all_now()
    health = monitor.health("prod-cn")
    assert health.state == "degraded"
    assert health.consecutive_failures == 1
    monitor.ensure_usable("prod-cn")  # degraded 仍允許新任務

    clock.advance(60)
    monitor.check_all_now()
    assert monitor.health("prod-cn").state == "active"
    assert monitor.health("prod-cn").consecutive_failures == 0


def test_transient_failure_beyond_grace_becomes_blocked():
    clock = FakeClock()
    monitor = make_monitor(
        make_snapshot(make_profile()),
        clock,
        {"prod-cn": [ok(), timeout(), timeout()]},
        settings_changes={"health_grace_sec": 100},
    )
    monitor.check_all_now()

    clock.advance(50)
    monitor.check_all_now()
    assert monitor.health("prod-cn").state == "degraded"

    clock.advance(60)  # 首次失敗至今 110s > grace 100s
    monitor.check_all_now()
    assert monitor.health("prod-cn").state == "blocked"


def test_disabled_profile_is_disabled_and_never_checked():
    clock = FakeClock()
    profile = make_profile(enabled=False)
    monitor = make_monitor(
        make_snapshot(profile), clock, {"prod-cn": []},  # 不應被呼叫
    )

    monitor.check_all_now()

    assert monitor.health("prod-cn").state == "disabled"


def test_unknown_profile_is_unavailable():
    clock = FakeClock()
    monitor = make_monitor(make_snapshot(make_profile()), clock, {"prod-cn": [ok()]})
    monitor.check_all_now()

    with pytest.raises(ProfileUnavailable, match="unknown profile"):
        monitor.ensure_usable("ghost")


def test_monitor_never_switches_profiles():
    """規格 §14：Health Monitor 不得自動改用其他 profile；只能回報狀態。"""
    clock = FakeClock()
    snapshot = make_snapshot(make_profile(), make_profile("backup"))
    monitor = make_monitor(
        snapshot, clock,
        {"prod-cn": [ok(account="999999999999")], "backup": [ok()]},
    )

    monitor.check_all_now()

    assert monitor.health("prod-cn").state == "blocked"
    assert monitor.health("backup").state == "active"
    # 對 blocked profile 的拒絕不含任何替代建議
    with pytest.raises(ProfileUnavailable) as exc_info:
        monitor.ensure_usable("prod-cn")
    assert "backup" not in str(exc_info.value)


def test_config_reload_reconciles_profile_set():
    clock = FakeClock()
    first = make_snapshot(make_profile(), generation=1)
    monitor = make_monitor(first, clock, {"prod-cn": [ok()]})
    monitor.check_all_now()
    assert monitor.health("prod-cn").state == "active"

    # 熱載入：prod-cn 被移除，新增 eu
    second = make_snapshot(make_profile("eu"), generation=2)
    monitor.on_config_reload(second)

    statuses = monitor.statuses()
    assert "prod-cn" not in statuses
    assert statuses["eu"].state == "active"  # 新 profile 在首次檢查前樂觀視為 active
