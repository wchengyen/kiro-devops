from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .models import ProfileConfig
from .runtime_env import build_profile_env


@dataclass(frozen=True)
class StsResult:
    ok: bool
    account_id: str | None
    # None | "timeout" | "profile_not_found" | "transient"
    error_kind: str | None
    detail: str


def mask_account_id(account_id: str) -> str:
    """規格 §7.4：固定顯示最後 4 位，例如 ********9012。"""
    return "*" * max(0, len(account_id) - 4) + account_id[-4:]


def _classify_failure(exc: Exception | None, returncode: int, stderr: str) -> str:
    if exc is not None:
        return "timeout"
    lowered = stderr.lower()
    if "could not be found" in lowered and "profile" in lowered:
        return "profile_not_found"
    return "transient"


def run_sts_check(
    profile: ProfileConfig,
    *,
    base_env: Mapping[str, str] | None = None,
    runner: Callable[..., Any] = subprocess.run,
    timeout_sec: int = 10,
) -> StsResult:
    """在隔離子環境執行 STS；永不修改 os.environ 或 base_env。"""
    env = build_profile_env(profile, base_env)
    argv = [
        "aws", "sts", "get-caller-identity",
        "--profile", profile.aws_profile, "--output", "json",
    ]
    try:
        completed = runner(
            argv, capture_output=True, text=True, timeout=timeout_sec, env=env,
        )
    except subprocess.TimeoutExpired:
        return StsResult(False, None, "timeout", f"sts timeout after {timeout_sec}s")
    except OSError as exc:
        return StsResult(False, None, "transient", f"aws cli spawn failed: {exc}")

    if completed.returncode != 0:
        kind = _classify_failure(None, completed.returncode, completed.stderr or "")
        # 只記錄錯誤摘要，不記錄完整 stderr（可能含 endpoint 等雜訊）
        detail = (completed.stderr or "").strip().splitlines()
        return StsResult(False, None, kind, detail[-1][:200] if detail else "sts failed")

    try:
        payload = json.loads(completed.stdout)
        account_id = payload["Account"]
    except (ValueError, KeyError, TypeError):
        return StsResult(False, None, "transient", "sts output is not valid identity json")
    return StsResult(True, str(account_id), None, "ok")
