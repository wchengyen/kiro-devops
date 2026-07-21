from multi_profile.models import AppConfig, ProfileConfig, RouteConfig, create_snapshot
from multi_profile.poll_sets import poll_chat_ids_for_app


def make_snapshot(routes):
    apps = {
        key: AppConfig(
            app_key=key,
            app_id_env="APP_ID",
            app_secret_env="APP_SECRET",
            default_profile="prod-cn",
        )
        for key in ("app-a", "app-b")
    }
    profiles = {
        "prod-cn": ProfileConfig(
            profile_id="prod-cn",
            aws_profile="production",
            expected_account_id="123456789012",
            working_dir="/tmp",
        )
    }
    return create_snapshot(1, apps, profiles, tuple(routes))


def test_only_poll_alerts_routes_are_included():
    snapshot = make_snapshot([
        RouteConfig("app-a", "oc_1", "prod-cn", poll_alerts=True),
        RouteConfig("app-a", "oc_2", "prod-cn", poll_alerts=False),
    ])
    assert poll_chat_ids_for_app(snapshot, "app-a") == ["oc_1"]


def test_poll_set_is_scoped_per_app():
    snapshot = make_snapshot([
        RouteConfig("app-a", "oc_shared", "prod-cn", poll_alerts=True),
        RouteConfig("app-b", "oc_shared", "prod-cn", poll_alerts=False),
        RouteConfig("app-b", "oc_b", "prod-cn", poll_alerts=True),
    ])
    assert poll_chat_ids_for_app(snapshot, "app-a") == ["oc_shared"]
    assert poll_chat_ids_for_app(snapshot, "app-b") == ["oc_b"]


def test_unknown_app_returns_empty_list():
    snapshot = make_snapshot([])
    assert poll_chat_ids_for_app(snapshot, "missing") == []


def test_result_is_sorted_and_deduplicated():
    snapshot = make_snapshot([
        RouteConfig("app-a", "oc_z", "prod-cn", poll_alerts=True),
        RouteConfig("app-a", "oc_a", "prod-cn", poll_alerts=True),
    ])
    assert poll_chat_ids_for_app(snapshot, "app-a") == ["oc_a", "oc_z"]
