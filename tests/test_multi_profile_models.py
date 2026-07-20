from dataclasses import FrozenInstanceError, replace

import pytest

from multi_profile.models import (
    AppConfig,
    ConfigSnapshot,
    ExecutionContext,
    ProfileConfig,
    RouteConfig,
    build_profile_fingerprint,
    create_snapshot,
)


def test_profile_defaults_match_schema():
    profile = ProfileConfig(
        profile_id="prod-cn",
        aws_profile="production",
        expected_account_id="123456789012",
        working_dir="/srv/kiro-devops",
    )

    assert profile.enabled is True
    assert profile.aws_region is None
    assert profile.kiro_agent is None
    assert profile.model is None
    assert profile.alert_agent == "ec2-alert-analyzer"
    assert profile.alert_model is None
    assert profile.sync_timeout == 120
    assert profile.async_timeout == 1800
    assert profile.alert_timeout == 300


def test_models_are_frozen():
    app = AppConfig(
        app_key="ops-bot",
        app_id_env="FEISHU_OPS_APP_ID",
        app_secret_env="FEISHU_OPS_APP_SECRET",
        default_profile="prod-cn",
    )

    with pytest.raises(FrozenInstanceError):
        app.default_profile = "other"


def test_snapshot_copies_and_protects_mappings():
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
    routes = (
        RouteConfig(
            app_key="ops-bot",
            chat_id="oc_prod",
            profile_id="prod-cn",
        ),
    )

    snapshot = create_snapshot(1, apps, profiles, routes)
    apps.clear()

    assert tuple(snapshot.apps) == ("ops-bot",)
    with pytest.raises(TypeError):
        snapshot.apps["other"] = snapshot.apps["ops-bot"]


def test_fingerprint_changes_only_for_session_sensitive_fields():
    profile = ProfileConfig(
        profile_id="prod-cn",
        aws_profile="production",
        aws_region="cn-northwest-1",
        expected_account_id="123456789012",
        kiro_agent="my-dev-bot",
        model="claude-sonnet",
        working_dir="/srv/kiro-devops",
    )
    original = build_profile_fingerprint(profile)

    assert build_profile_fingerprint(replace(profile, sync_timeout=240)) == original
    assert build_profile_fingerprint(replace(profile, aws_region="cn-north-1")) != original
    assert build_profile_fingerprint(replace(profile, kiro_agent="other")) != original


def test_execution_context_is_frozen():
    profile = ProfileConfig(
        profile_id="prod-cn",
        aws_profile="production",
        expected_account_id="123456789012",
        working_dir="/srv/kiro-devops",
    )
    context = ExecutionContext(
        config_generation=1,
        platform="feishu",
        app_key="ops-bot",
        chat_type="group",
        chat_id="oc_prod",
        user_id="ou_user",
        principal_key="feishu/ops-bot/group/oc_prod/user/ou_user",
        group_scope_key="feishu/ops-bot/group/oc_prod",
        profile_id="prod-cn",
        profile=profile,
        profile_fingerprint=build_profile_fingerprint(profile),
    )

    with pytest.raises(FrozenInstanceError):
        context.profile_id = "other"
