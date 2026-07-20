from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class AppConfig:
    app_key: str
    app_id_env: str
    app_secret_env: str
    default_profile: str
    enabled: bool = True


@dataclass(frozen=True)
class ProfileConfig:
    profile_id: str
    aws_profile: str
    expected_account_id: str
    working_dir: str
    enabled: bool = True
    aws_region: str | None = None
    kiro_agent: str | None = None
    model: str | None = None
    alert_agent: str = "ec2-alert-analyzer"
    alert_model: str | None = None
    sync_timeout: int = 120
    async_timeout: int = 1800
    alert_timeout: int = 300


@dataclass(frozen=True)
class RouteConfig:
    app_key: str
    chat_id: str
    profile_id: str
    poll_alerts: bool = False


@dataclass(frozen=True)
class ConfigSnapshot:
    version: int
    generation: int
    apps: Mapping[str, AppConfig]
    profiles: Mapping[str, ProfileConfig]
    routes: tuple[RouteConfig, ...]


@dataclass(frozen=True)
class ExecutionContext:
    config_generation: int
    platform: str
    app_key: str
    chat_type: str
    chat_id: str | None
    user_id: str
    principal_key: str
    group_scope_key: str | None
    profile_id: str
    profile: ProfileConfig
    profile_fingerprint: str


def build_profile_fingerprint(profile: ProfileConfig) -> str:
    payload = {
        "profile_id": profile.profile_id,
        "aws_profile": profile.aws_profile,
        "aws_region": profile.aws_region,
        "kiro_agent": profile.kiro_agent,
        "model": profile.model,
        "working_dir": profile.working_dir,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_snapshot(
    generation: int,
    apps: Mapping[str, AppConfig],
    profiles: Mapping[str, ProfileConfig],
    routes: tuple[RouteConfig, ...],
) -> ConfigSnapshot:
    return ConfigSnapshot(
        version=1,
        generation=generation,
        apps=MappingProxyType(dict(apps)),
        profiles=MappingProxyType(dict(profiles)),
        routes=tuple(routes),
    )
