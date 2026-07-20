import pytest

from multi_profile.models import ExecutionContext, ProfileConfig, build_profile_fingerprint
from multi_profile.session_store import SessionStore


def make_context(principal="principal-a", **profile_changes):
    values = {
        "profile_id": "prod-cn",
        "aws_profile": "production",
        "aws_region": "cn-northwest-1",
        "expected_account_id": "123456789012",
        "working_dir": "/srv/kiro-devops",
        "kiro_agent": "agent-a",
        "model": "model-a",
    }
    values.update(profile_changes)
    profile = ProfileConfig(**values)
    return ExecutionContext(
        config_generation=1,
        platform="feishu",
        app_key="ops-bot",
        chat_type="group",
        chat_id="oc_prod",
        user_id="ou_user",
        principal_key=principal,
        group_scope_key="feishu/ops-bot/group/oc_prod",
        profile_id=profile.profile_id,
        profile=profile,
        profile_fingerprint=build_profile_fingerprint(profile),
    )


def test_register_and_resolve_latest_session(tmp_path):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    context = make_context()

    record = store.register_new(context, "session-1", "first topic", now=100.0)

    assert record.short_id == 1
    assert store.resolve_active(context, now=200.0, timeout=1800).kiro_session_id == "session-1"


def test_different_principals_are_isolated(tmp_path):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    first = make_context("principal-a")
    second = make_context("principal-b")
    store.register_new(first, "session-a", "A", now=100.0)

    assert store.resolve_active(second, now=200.0, timeout=1800) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"profile_id": "other-profile"},
        {"aws_profile": "other-aws"},
        {"aws_region": "cn-north-1"},
        {"kiro_agent": "agent-b"},
        {"model": "model-b"},
        {"working_dir": "/srv/other"},
    ],
)
def test_latest_fingerprint_mismatch_forces_new_session(tmp_path, changes):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    original = make_context()
    changed = make_context(**changes)
    store.register_new(original, "session-a", "A", now=100.0)

    assert store.resolve_active(changed, now=200.0, timeout=1800) is None


def test_timeout_changes_do_not_invalidate_session(tmp_path):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    original = make_context()
    changed = make_context(sync_timeout=240, async_timeout=2400, alert_timeout=600)
    store.register_new(original, "session-a", "A", now=100.0)

    assert store.resolve_active(changed, now=200.0, timeout=1800).kiro_session_id == "session-a"


def test_expired_latest_session_does_not_fall_back_to_older_one(tmp_path):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    context = make_context()
    store.register_new(context, "session-old", "old", now=10.0)
    store.register_new(context, "session-latest", "latest", now=20.0)

    assert store.resolve_active(context, now=2000.0, timeout=1800) is None


def test_clear_active_expires_all_sessions_for_principal(tmp_path):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    context = make_context()
    store.register_new(context, "session-a", "A", now=100.0)
    store.clear_active(context.principal_key)

    assert store.resolve_active(context, now=101.0, timeout=1800) is None


def test_short_id_resume_requires_matching_fingerprint(tmp_path):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    original = make_context(model="model-a")
    changed = make_context(model="model-b")
    record = store.register_new(original, "session-a", "A", now=100.0)

    assert store.get_by_short_id(original, record.short_id) is not None
    assert store.get_by_short_id(changed, record.short_id) is None


def test_keeps_only_latest_twenty_sessions_per_principal(tmp_path):
    store = SessionStore(tmp_path / "tenant_sessions.db", max_sessions_per_principal=20)
    context = make_context()
    for index in range(25):
        store.register_new(context, f"session-{index}", str(index), now=float(index + 1))

    records = store.list_sessions(context, limit=100)

    assert len(records) == 20
    assert records[0].kiro_session_id == "session-24"
    assert records[-1].kiro_session_id == "session-5"
