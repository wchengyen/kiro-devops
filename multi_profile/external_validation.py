from __future__ import annotations

import configparser
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .config_loader import ConfigError, load_config
from .models import ConfigSnapshot
from .sts import StsResult, mask_account_id, run_sts_check


@dataclass(frozen=True)
class StageResult:
    stage: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    stages: tuple[StageResult, ...]
    snapshot: ConfigSnapshot | None  # 僅成功時提供，供 publisher 重用


def _default_model_lister() -> list[str]:
    kiro_bin = shutil.which("kiro-cli") or "kiro-cli"
    try:
        result = subprocess.run(
            [kiro_bin, "chat", "--list-models", "--format", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        models = data.get("models", data if isinstance(data, list) else [])
        return [m.get("id") or m.get("name") for m in models if isinstance(m, dict)]
    except Exception:
        return []


def _load_snapshot_from_text(
    yaml_text: str, environ: Mapping[str, str],
) -> tuple[ConfigSnapshot | None, str | None, str | None]:
    """把 Draft 寫入暫存檔並重用計畫 1 的 load_config（步驟 1–4）。

    回傳 (snapshot, failed_stage, detail)；成功時後兩者為 None。
    """
    fd, tmp_name = tempfile.mkstemp(prefix="draft-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(yaml_text)
        try:
            return load_config(tmp_name, environ=environ, generation=0), None, None
        except ConfigError as exc:
            message = str(exc)
            if message.startswith("invalid YAML") or "unknown field" in message \
                    or "must be" in message and "env" not in message:
                # YAML 語法與 schema 類錯誤
                stage = "yaml_schema"
            elif "env" in message:
                stage = "env_refs"
            elif "references" in message or "duplicate route" in message:
                stage = "referential_integrity"
            else:
                stage = "paths_timeouts"
            return None, stage, message
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def _check_kiro_agent_model(
    snapshot: ConfigSnapshot,
    kiro_agents_dir: Path,
    model_lister: Callable[[], list[str]],
) -> StageResult:
    agents = {
        path.stem for path in Path(kiro_agents_dir).glob("*.json")
    } if Path(kiro_agents_dir).is_dir() else set()
    needed = set()
    for profile in snapshot.profiles.values():
        if profile.enabled and profile.kiro_agent:
            needed.add(profile.kiro_agent)
    # 注意：alert_agent 在計畫 1 模型中有預設值 "ec2-alert-analyzer"，
    # 無法區分「顯式設定」與「預設」，因此只做模型核對、不要求 agent 檔存在
    missing = sorted(needed - agents)
    if missing:
        return StageResult(
            "kiro_agent_model", False, f"missing kiro agent(s): {', '.join(missing)}",
        )

    wanted_models = set()
    for profile in snapshot.profiles.values():
        if profile.enabled:
            for model in (profile.model, profile.alert_model):
                if model:
                    wanted_models.add(model)
    if wanted_models:
        available = set(model_lister())
        unavailable = sorted(wanted_models - available)
        if unavailable:
            return StageResult(
                "kiro_agent_model", False,
                f"unavailable model(s): {', '.join(unavailable)}",
            )
    return StageResult("kiro_agent_model", True, "agents and models available")


def _check_aws_cli_profiles(
    snapshot: ConfigSnapshot, aws_config_dir: Path,
) -> StageResult:
    parser = configparser.RawConfigParser()
    parser.read([
        Path(aws_config_dir) / "credentials",
        Path(aws_config_dir) / "config",
    ])
    known = set()
    for section in parser.sections():
        known.add(section)
        if section.startswith("profile "):
            known.add(section[len("profile "):])
    needed = {
        p.aws_profile for p in snapshot.profiles.values() if p.enabled
    }
    missing = sorted(needed - known)
    if missing:
        return StageResult(
            "aws_cli_profile", False,
            f"missing AWS CLI profile(s): {', '.join(missing)}",
        )
    return StageResult("aws_cli_profile", True, "all AWS CLI profiles exist")


def run_validation_pipeline(
    yaml_text: str,
    *,
    environ: Mapping[str, str] | None = None,
    kiro_agents_dir: str | Path | None = None,
    aws_config_dir: str | Path | None = None,
    model_lister: Callable[[], list[str]] | None = None,
    sts_runner: Callable[..., StsResult] = run_sts_check,
    sts_timeout_sec: int = 10,
) -> ValidationReport:
    """規格 §13.3 完整驗證；任何一步失敗即停止，外部階段不重試。"""
    environ = environ if environ is not None else os.environ
    kiro_agents_dir = Path(kiro_agents_dir or Path.home() / ".kiro" / "agents")
    aws_config_dir = Path(aws_config_dir or Path.home() / ".aws")
    model_lister = model_lister or _default_model_lister

    snapshot, failed_stage, detail = _load_snapshot_from_text(yaml_text, environ)
    if snapshot is None:
        # 步驟 1–4 由 loader 一次完成；回報失敗的那一階段
        prefix_stages = ["yaml_schema", "env_refs", "referential_integrity", "paths_timeouts"]
        stages = tuple(
            StageResult(name, False, detail if name == failed_stage else "skipped")
            if name == failed_stage else StageResult(name, True, "ok")
            for name in prefix_stages[: prefix_stages.index(failed_stage) + 1]
        )
        return ValidationReport(False, stages, None)

    stages = [
        StageResult("yaml_schema", True, "ok"),
        StageResult("env_refs", True, "ok"),
        StageResult("referential_integrity", True, "ok"),
        StageResult("paths_timeouts", True, "ok"),
    ]

    kiro_stage = _check_kiro_agent_model(snapshot, kiro_agents_dir, model_lister)
    stages.append(kiro_stage)
    if not kiro_stage.ok:
        return ValidationReport(False, tuple(stages), None)

    aws_stage = _check_aws_cli_profiles(snapshot, aws_config_dir)
    stages.append(aws_stage)
    if not aws_stage.ok:
        return ValidationReport(False, tuple(stages), None)

    for profile in snapshot.profiles.values():
        if not profile.enabled:
            continue
        result = sts_runner(profile, timeout_sec=sts_timeout_sec)
        if not result.ok:
            stages.append(StageResult(
                "sts_identity", False,
                f"{profile.profile_id}: sts {result.error_kind}: {result.detail}",
            ))
            return ValidationReport(False, tuple(stages), None)
        if result.account_id != profile.expected_account_id:
            stages.append(StageResult(
                "expected_account", False,
                f"{profile.profile_id}: expected "
                f"{mask_account_id(profile.expected_account_id)} but got "
                f"{mask_account_id(result.account_id or '')}",
            ))
            return ValidationReport(False, tuple(stages), None)

    stages.append(StageResult("sts_identity", True, "ok"))
    stages.append(StageResult("expected_account", True, "ok"))
    return ValidationReport(True, tuple(stages), snapshot)
