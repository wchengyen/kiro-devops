from event_store import EventStore
from memory import MemoryLayer
from multi_profile.models import AppConfig, ProfileConfig, RouteConfig, create_snapshot
from multi_profile.router import TenantRouter
from multi_profile.scoped_state import event_owner, scoped_event_id, semantic_owner


def build_contexts(tmp_path):
    apps = {
        key: AppConfig(
            app_key=key,
            app_id_env=f"{key.upper().replace('-', '_')}_ID",
            app_secret_env=f"{key.upper().replace('-', '_')}_SECRET",
            default_profile="prod-cn",
        )
        for key in ("app-a", "app-b")
    }
    profile = ProfileConfig(
        profile_id="prod-cn",
        aws_profile="production",
        expected_account_id="123456789012",
        working_dir=str(tmp_path),
    )
    snapshot = create_snapshot(
        1,
        apps,
        {"prod-cn": profile},
        (
            RouteConfig("app-a", "oc_shared", "prod-cn"),
            RouteConfig("app-b", "oc_shared", "prod-cn"),
            RouteConfig("app-a", "oc_other", "prod-cn"),
        ),
    )
    router = TenantRouter(snapshot)
    common = {
        "platform": "feishu",
        "chat_type": "group",
        "user_id": "ou_same_user",
    }
    return (
        router.resolve(app_key="app-a", chat_id="oc_shared", **common),
        router.resolve(app_key="app-b", chat_id="oc_shared", **common),
        router.resolve(app_key="app-a", chat_id="oc_other", **common),
    )


def test_scope_owners_come_from_router_context(tmp_path):
    app_a, app_b, other_group = build_contexts(tmp_path)

    assert semantic_owner(app_a) != semantic_owner(app_b)
    assert semantic_owner(app_a) != semantic_owner(other_group)
    assert event_owner(app_a) != event_owner(app_b)
    assert event_owner(app_a) != event_owner(other_group)


def test_same_external_event_id_is_scoped_per_group(tmp_path):
    app_a, app_b, other_group = build_contexts(tmp_path)

    first = scoped_event_id(app_a, "prometheus-alert-1")
    second = scoped_event_id(app_b, "prometheus-alert-1")
    third = scoped_event_id(other_group, "prometheus-alert-1")

    assert len({first, second, third}) == 3
    assert first == scoped_event_id(app_a, "prometheus-alert-1")


def test_semantic_memory_characterization_uses_principal_scope(tmp_path):
    memory = MemoryLayer(db_path=str(tmp_path / "memory"))
    app_a, app_b, other_group = build_contexts(tmp_path)
    memory.add(semantic_owner(app_a), "app A group secret")

    assert memory.list_all(semantic_owner(app_a)) == ["app A group secret"]
    assert memory.list_all(semantic_owner(app_b)) == []
    assert memory.list_all(semantic_owner(other_group)) == []


def test_event_store_characterization_preserves_same_external_id_per_scope(tmp_path):
    events = EventStore(tmp_path / "events.db")
    app_a, app_b, _ = build_contexts(tmp_path)
    external_id = "prometheus-alert-1"

    events.add_event(
        user_id=event_owner(app_a),
        event_id=scoped_event_id(app_a, external_id),
        title="app A deployment",
        event_type="应用发版",
    )
    events.add_event(
        user_id=event_owner(app_b),
        event_id=scoped_event_id(app_b, external_id),
        title="app B deployment",
        event_type="应用发版",
    )

    assert len(events.search_events(event_owner(app_a), query="deployment")) == 1
    assert len(events.search_events(event_owner(app_b), query="deployment")) == 1
