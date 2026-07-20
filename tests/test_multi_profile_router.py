import pytest

from multi_profile.models import AppConfig, ProfileConfig, RouteConfig, create_snapshot
from multi_profile.router import RouteNotFound, TenantRouter


@pytest.fixture
def snapshot(tmp_path):
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
            working_dir=str(tmp_path),
        )
    }
    routes = (
        RouteConfig(
            app_key="ops-bot",
            chat_id="oc_prod_a",
            profile_id="prod-cn",
        ),
        RouteConfig(
            app_key="ops-bot",
            chat_id="oc_prod_b",
            profile_id="prod-cn",
        ),
    )
    return create_snapshot(3, apps, profiles, routes)


def test_group_route_builds_group_and_principal_keys(snapshot):
    context = TenantRouter(snapshot).resolve(
        platform="feishu",
        app_key="ops-bot",
        chat_type="group",
        chat_id="oc_prod_a",
        user_id="ou_user",
    )

    assert context.profile_id == "prod-cn"
    assert context.config_generation == 3
    assert context.group_scope_key == "feishu/ops-bot/group/oc_prod_a"
    assert context.principal_key == "feishu/ops-bot/group/oc_prod_a/user/ou_user"


def test_same_profile_different_groups_have_different_principals(snapshot):
    router = TenantRouter(snapshot)

    first = router.resolve(
        platform="feishu",
        app_key="ops-bot",
        chat_type="group",
        chat_id="oc_prod_a",
        user_id="ou_user",
    )
    second = router.resolve(
        platform="feishu",
        app_key="ops-bot",
        chat_type="group",
        chat_id="oc_prod_b",
        user_id="ou_user",
    )

    assert first.profile_id == second.profile_id
    assert first.principal_key != second.principal_key


def test_private_chat_uses_app_default_profile(snapshot):
    context = TenantRouter(snapshot).resolve(
        platform="feishu",
        app_key="ops-bot",
        chat_type="private",
        chat_id=None,
        user_id="ou_user",
    )

    assert context.profile_id == "prod-cn"
    assert context.group_scope_key is None
    assert context.principal_key == "feishu/ops-bot/private/ou_user"


def test_unmapped_group_fails_closed(snapshot):
    with pytest.raises(RouteNotFound, match="unmapped group"):
        TenantRouter(snapshot).resolve(
            platform="feishu",
            app_key="ops-bot",
            chat_type="group",
            chat_id="oc_unknown",
            user_id="ou_user",
        )


def test_unknown_app_private_chat_fails_closed(snapshot):
    with pytest.raises(RouteNotFound, match="unknown app"):
        TenantRouter(snapshot).resolve(
            platform="feishu",
            app_key="missing",
            chat_type="private",
            chat_id=None,
            user_id="ou_user",
        )
