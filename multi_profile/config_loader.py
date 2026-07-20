from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

import yaml

from .models import AppConfig, ProfileConfig, RouteConfig, create_snapshot


class ConfigError(ValueError):
    pass


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ROOT_FIELDS = {"version", "apps", "profiles", "routes"}
_APP_FIELDS = {"enabled", "app_id_env", "app_secret_env", "default_profile"}
_PROFILE_FIELDS = {
    "enabled",
    "aws_profile",
    "aws_region",
    "expected_account_id",
    "kiro_agent",
    "model",
    "alert_agent",
    "alert_model",
    "working_dir",
    "sync_timeout",
    "async_timeout",
    "alert_timeout",
}
_ROUTE_FIELDS = {"app", "chat_id", "profile", "poll_alerts"}


def _mapping(value, path: str) -> dict:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} must be a mapping")
    data = dict(value)
    if any(not isinstance(key, str) for key in data):
        raise ConfigError(f"{path} keys must be strings")
    return data


def _list(value, path: str) -> list:
    if not isinstance(value, list):
        raise ConfigError(f"{path} must be a list")
    return value


def _reject_unknown(data: dict, allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(f"{path}: unknown field(s): {', '.join(unknown)}")


def _required_string(data: dict, field: str, path: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}.{field} must be a non-empty string")
    return value.strip()


def _optional_string(data: dict, field: str, path: str) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}.{field} must be null or a non-empty string")
    return value.strip()


def _boolean(data: dict, field: str, default: bool, path: str) -> bool:
    value = data.get(field, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{path}.{field} must be a boolean")
    return value


def _integer(data: dict, field: str, default: int, path: str) -> int:
    value = data.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path}.{field} must be an integer")
    return value


def _parse_app(app_key: str, value) -> AppConfig:
    path = f"apps.{app_key}"
    data = _mapping(value, path)
    _reject_unknown(data, _APP_FIELDS, path)
    return AppConfig(
        app_key=app_key,
        enabled=_boolean(data, "enabled", True, path),
        app_id_env=_required_string(data, "app_id_env", path),
        app_secret_env=_required_string(data, "app_secret_env", path),
        default_profile=_required_string(data, "default_profile", path),
    )


def _parse_profile(profile_id: str, value) -> ProfileConfig:
    path = f"profiles.{profile_id}"
    data = _mapping(value, path)
    _reject_unknown(data, _PROFILE_FIELDS, path)
    return ProfileConfig(
        profile_id=profile_id,
        enabled=_boolean(data, "enabled", True, path),
        aws_profile=_required_string(data, "aws_profile", path),
        aws_region=_optional_string(data, "aws_region", path),
        expected_account_id=_required_string(data, "expected_account_id", path),
        kiro_agent=_optional_string(data, "kiro_agent", path),
        model=_optional_string(data, "model", path),
        alert_agent=_optional_string(data, "alert_agent", path) or "ec2-alert-analyzer",
        alert_model=_optional_string(data, "alert_model", path),
        working_dir=_required_string(data, "working_dir", path),
        sync_timeout=_integer(data, "sync_timeout", 120, path),
        async_timeout=_integer(data, "async_timeout", 1800, path),
        alert_timeout=_integer(data, "alert_timeout", 300, path),
    )


def _parse_route(index: int, value) -> RouteConfig:
    path = f"routes[{index}]"
    data = _mapping(value, path)
    _reject_unknown(data, _ROUTE_FIELDS, path)
    return RouteConfig(
        app_key=_required_string(data, "app", path),
        chat_id=_required_string(data, "chat_id", path),
        profile_id=_required_string(data, "profile", path),
        poll_alerts=_boolean(data, "poll_alerts", False, path),
    )


def load_config(
    path: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
    generation: int = 1,
):
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc

    root = _mapping(raw, "config")
    _reject_unknown(root, _ROOT_FIELDS, "config")
    version = root.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ConfigError("config.version must be integer 1")

    raw_apps = _mapping(root.get("apps"), "apps")
    raw_profiles = _mapping(root.get("profiles"), "profiles")
    raw_routes = _list(root.get("routes"), "routes")
    if not raw_apps:
        raise ConfigError("apps must not be empty")
    if not raw_profiles:
        raise ConfigError("profiles must not be empty")

    apps = {key: _parse_app(key, value) for key, value in raw_apps.items()}
    profiles = {key: _parse_profile(key, value) for key, value in raw_profiles.items()}
    routes = tuple(_parse_route(i, value) for i, value in enumerate(raw_routes))

    _validate_config(
        apps,
        profiles,
        routes,
        environ if environ is not None else os.environ,
    )
    return create_snapshot(generation, apps, profiles, routes)


def _validate_config(apps, profiles, routes, environ: Mapping[str, str]) -> None:
    for app_key in apps:
        if not _ID_RE.fullmatch(app_key):
            raise ConfigError(f"invalid app key: {app_key}")
    for profile_id in profiles:
        if not _ID_RE.fullmatch(profile_id):
            raise ConfigError(f"invalid profile id: {profile_id}")

    for app in apps.values():
        for field_name, env_name in (
            ("app_id_env", app.app_id_env),
            ("app_secret_env", app.app_secret_env),
        ):
            if not _ENV_RE.fullmatch(env_name):
                raise ConfigError(f"apps.{app.app_key}.{field_name} has invalid env name")
            if app.enabled and not environ.get(env_name, "").strip():
                raise ConfigError(f"apps.{app.app_key}.{field_name} references missing env {env_name}")

        target = profiles.get(app.default_profile)
        if target is None:
            raise ConfigError(
                f"apps.{app.app_key}.default_profile references missing profile {app.default_profile}"
            )
        if not target.enabled:
            raise ConfigError(
                f"apps.{app.app_key}.default_profile references disabled profile {app.default_profile}"
            )

    for profile in profiles.values():
        path = f"profiles.{profile.profile_id}"
        if not re.fullmatch(r"\d{12}", profile.expected_account_id):
            raise ConfigError(f"{path}.expected_account_id must be 12 digits")
        working_dir = Path(profile.working_dir)
        if not working_dir.is_absolute() or not working_dir.is_dir():
            raise ConfigError(f"{path}.working_dir must be an existing directory (absolute path required)")
        if not os.access(working_dir, os.R_OK | os.X_OK):
            raise ConfigError(f"{path}.working_dir is not readable by the service user")
        if not 10 <= profile.sync_timeout <= 600:
            raise ConfigError(f"{path}.sync_timeout must be between 10 and 600")
        if not profile.sync_timeout <= profile.async_timeout <= 86400:
            raise ConfigError(
                f"{path}.async_timeout must be between sync_timeout and 86400"
            )
        if not 10 <= profile.alert_timeout <= 3600:
            raise ConfigError(f"{path}.alert_timeout must be between 10 and 3600")

    seen_routes: set[tuple[str, str]] = set()
    for index, route in enumerate(routes):
        key = (route.app_key, route.chat_id)
        if key in seen_routes:
            raise ConfigError(f"duplicate route for {route.app_key} {route.chat_id}")
        seen_routes.add(key)

        app = apps.get(route.app_key)
        if app is None:
            raise ConfigError(f"routes[{index}] references missing app {route.app_key}")
        if not app.enabled:
            raise ConfigError(f"routes[{index}] references disabled app {route.app_key}")
        profile = profiles.get(route.profile_id)
        if profile is None:
            raise ConfigError(f"routes[{index}] references missing profile {route.profile_id}")
        if not profile.enabled:
            raise ConfigError(f"routes[{index}] references disabled profile {route.profile_id}")
