from multi_profile.models import ExecutionContext, ProfileConfig, build_profile_fingerprint
from multi_profile.runtime_env import build_child_env, build_kiro_command


def make_context(**profile_changes):
    values = {
        "profile_id": "prod-cn",
        "aws_profile": "production",
        "aws_region": "cn-northwest-1",
        "expected_account_id": "123456789012",
        "kiro_agent": "my-dev-bot",
        "model": "claude-sonnet",
        "working_dir": "/srv/kiro-devops",
    }
    values.update(profile_changes)
    profile = ProfileConfig(**values)
    return ExecutionContext(
        config_generation=2,
        platform="feishu",
        app_key="ops-bot",
        chat_type="group",
        chat_id="oc_prod",
        user_id="ou_user",
        principal_key="feishu/ops-bot/group/oc_prod/user/ou_user",
        group_scope_key="feishu/ops-bot/group/oc_prod",
        profile_id=profile.profile_id,
        profile=profile,
        profile_fingerprint=build_profile_fingerprint(profile),
    )


def test_child_env_removes_parent_aws_credentials_without_mutating_input():
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/ubuntu",
        "AWS_ACCESS_KEY_ID": "wrong-key",
        "AWS_SECRET_ACCESS_KEY": "wrong-secret",
        "AWS_SESSION_TOKEN": "wrong-token",
        "AWS_PROFILE": "wrong-profile",
        "AWS_DEFAULT_PROFILE": "wrong-default",
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
    }

    child = build_child_env(make_context(), base)

    assert base["AWS_ACCESS_KEY_ID"] == "wrong-key"
    assert "AWS_ACCESS_KEY_ID" not in child
    assert "AWS_SECRET_ACCESS_KEY" not in child
    assert "AWS_SESSION_TOKEN" not in child
    assert child["AWS_PROFILE"] == "production"
    assert child["AWS_DEFAULT_PROFILE"] == "production"
    assert child["AWS_REGION"] == "cn-northwest-1"
    assert child["AWS_DEFAULT_REGION"] == "cn-northwest-1"
    assert child["AWS_SDK_LOAD_CONFIG"] == "1"
    assert child["NO_COLOR"] == "1"


def test_child_env_omits_region_when_profile_does_not_override_it():
    child = build_child_env(
        make_context(aws_region=None),
        {"AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "us-east-1"},
    )

    assert "AWS_REGION" not in child
    assert "AWS_DEFAULT_REGION" not in child


def test_new_session_command_has_no_resume_flag():
    command = build_kiro_command("/usr/bin/kiro-cli", make_context(), "hello", None)

    assert "--resume" not in command
    assert "--resume-id" not in command
    assert command[-1] == "hello"


def test_existing_session_uses_exact_resume_id():
    command = build_kiro_command(
        "/usr/bin/kiro-cli",
        make_context(),
        "continue",
        "11111111-1111-1111-1111-111111111111",
    )

    index = command.index("--resume-id")
    assert command[index + 1] == "11111111-1111-1111-1111-111111111111"
    assert "--resume" not in command


def test_configured_agent_and_model_are_passed_exactly():
    command = build_kiro_command(
        "/usr/bin/kiro-cli",
        make_context(kiro_agent="my-dev-bot", model="claude-sonnet"),
        "hello",
        None,
    )

    assert command[command.index("--agent") + 1] == "my-dev-bot"
    assert command[command.index("--model") + 1] == "claude-sonnet"


def test_optional_agent_and_model_are_omitted():
    command = build_kiro_command(
        "/usr/bin/kiro-cli",
        make_context(kiro_agent=None, model=None),
        "hello",
        None,
    )

    assert "--agent" not in command
    assert "--model" not in command
