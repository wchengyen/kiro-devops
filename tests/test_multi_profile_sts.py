import json
import subprocess

import pytest

from multi_profile.models import ProfileConfig
from multi_profile.sts import (
    StsResult,
    mask_account_id,
    run_sts_check,
)


def make_profile(**changes):
    values = {
        "profile_id": "prod-cn",
        "aws_profile": "production",
        "aws_region": "cn-northwest-1",
        "expected_account_id": "123456789012",
        "working_dir": "/srv/kiro-devops",
    }
    values.update(changes)
    return ProfileConfig(**values)


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["aws"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


def test_mask_account_id_shows_only_last_four_digits():
    assert mask_account_id("123456789012") == "********9012"
    assert len(mask_account_id("123456789012")) == 12


def test_sts_success_returns_account_id():
    seen = {}

    def runner(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs["env"]
        seen["timeout"] = kwargs["timeout"]
        return completed(stdout=json.dumps({"Account": "123456789012"}))

    result = run_sts_check(
        make_profile(),
        base_env={"PATH": "/usr/bin", "AWS_ACCESS_KEY_ID": "AKIA_LEAK"},
        runner=runner,
        timeout_sec=10,
    )

    assert result.ok is True
    assert result.account_id == "123456789012"
    assert result.error_kind is None
    assert seen["argv"] == [
        "aws", "sts", "get-caller-identity",
        "--profile", "production", "--output", "json",
    ]
    assert seen["timeout"] == 10
    # 隔離環境：父程序 credential selectors 被移除，且不修改 base_env
    assert "AWS_ACCESS_KEY_ID" not in seen["env"]
    assert seen["env"]["AWS_PROFILE"] == "production"
    assert seen["env"]["AWS_DEFAULT_PROFILE"] == "production"
    assert seen["env"]["AWS_REGION"] == "cn-northwest-1"


def test_sts_check_does_not_mutate_base_env_or_os_environ():
    base = {"PATH": "/usr/bin", "AWS_PROFILE": "wrong-profile"}

    def runner(argv, **kwargs):
        return completed(stdout=json.dumps({"Account": "123456789012"}))

    run_sts_check(make_profile(), base_env=base, runner=runner, timeout_sec=10)

    assert base == {"PATH": "/usr/bin", "AWS_PROFILE": "wrong-profile"}


def test_sts_timeout_is_classified_transient():
    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd="aws", timeout=10)

    result = run_sts_check(make_profile(), base_env={}, runner=runner, timeout_sec=10)

    assert result.ok is False
    assert result.error_kind == "timeout"
    assert result.account_id is None


def test_missing_aws_cli_profile_is_classified_permanent():
    def runner(argv, **kwargs):
        return completed(returncode=255, stderr="The config profile (ghost) could not be found")

    result = run_sts_check(make_profile(), base_env={}, runner=runner, timeout_sec=10)

    assert result.ok is False
    assert result.error_kind == "profile_not_found"


def test_other_aws_failure_is_classified_transient():
    def runner(argv, **kwargs):
        return completed(returncode=255, stderr="Unable to locate credentials")

    result = run_sts_check(make_profile(), base_env={}, runner=runner, timeout_sec=10)

    assert result.ok is False
    assert result.error_kind == "transient"


def test_unparseable_output_is_transient_failure():
    def runner(argv, **kwargs):
        return completed(stdout="<html>proxy error</html>")

    result = run_sts_check(make_profile(), base_env={}, runner=runner, timeout_sec=10)

    assert result.ok is False
    assert result.error_kind == "transient"


def test_sts_env_comes_from_shared_profile_env_builder():
    """STS 與 Kiro 子程序必須使用同一套環境隔離規則（規格 §9.1）。"""
    from multi_profile.runtime_env import build_profile_env

    env = build_profile_env(
        make_profile(),
        {"PATH": "/usr/bin", "AWS_SESSION_TOKEN": "leak"},
    )

    assert "AWS_SESSION_TOKEN" not in env
    assert env["AWS_PROFILE"] == "production"
    assert env["AWS_SDK_LOAD_CONFIG"] == "1"
