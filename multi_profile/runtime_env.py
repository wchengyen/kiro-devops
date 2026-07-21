from __future__ import annotations

import os
from collections.abc import Mapping

from .models import ExecutionContext, ProfileConfig


_AWS_SELECTOR_VARS = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
}


def build_profile_env(
    profile: ProfileConfig,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    child = dict(os.environ if base_env is None else base_env)
    for name in _AWS_SELECTOR_VARS:
        child.pop(name, None)

    child["AWS_PROFILE"] = profile.aws_profile
    child["AWS_DEFAULT_PROFILE"] = profile.aws_profile
    if profile.aws_region:
        child["AWS_REGION"] = profile.aws_region
        child["AWS_DEFAULT_REGION"] = profile.aws_region
    child["AWS_SDK_LOAD_CONFIG"] = "1"
    child["NO_COLOR"] = "1"
    return child


def build_child_env(
    context: ExecutionContext,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    return build_profile_env(context.profile, base_env)


def build_kiro_command(
    kiro_bin: str,
    context: ExecutionContext,
    prompt: str,
    session_id: str | None,
) -> list[str]:
    command = [
        kiro_bin,
        "chat",
        "--no-interactive",
        "-a",
        "--trust-tools=execute_bash",
        "--wrap",
        "never",
    ]
    if session_id:
        command += ["--resume-id", session_id]
    if context.profile.kiro_agent:
        command += ["--agent", context.profile.kiro_agent]
    if context.profile.model:
        command += ["--model", context.profile.model]
    command.append(prompt)
    return command
