from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .health import ProfileHealthMonitor
from .operational_settings import OperationalSettings
from .publisher import LastActionResult
from .registry import ConfigRegistry
from .revisions import config_checksum
from .task_registry import TaskRegistry


def _action_dict(result: LastActionResult | None) -> dict | None:
    return asdict(result) if result is not None else None


def build_multi_profile_status(
    *,
    mode: str,
    registry: ConfigRegistry | None,
    config_text: str | None,
    health_monitor: ProfileHealthMonitor | None,
    app_manager: Any,
    task_registry: TaskRegistry | None,
    settings: OperationalSettings,
    last_load: LastActionResult | None,
    last_publish: LastActionResult | None,
    last_rollback: LastActionResult | None,
) -> dict:
    """規格 §17 可觀測性 payload。此端點僅供 Dashboard 驗證後使用，
    因此 profile 可包含完整 account_id；群內 /profile 仍只用遮罩值。"""
    status: dict[str, Any] = {
        "mode": mode,
        "generation": None,
        "checksum": None,
        "apps": {},
        "profiles": {},
        "tasks": {"total": 0, "by_profile": {}},
        "settings": {
            "sts_timeout_sec": settings.sts_timeout_sec,
            "health_check_interval_sec": settings.health_check_interval_sec,
            "health_grace_sec": settings.health_grace_sec,
            "health_jitter_max_sec": settings.health_jitter_max_sec,
            "revision_keep": settings.revision_keep,
        },
        "last_load": _action_dict(last_load),
        "last_publish": _action_dict(last_publish),
        "last_rollback": _action_dict(last_rollback),
    }

    if mode != "multi-profile" or registry is None:
        return status

    try:
        snapshot = registry.snapshot()
    except RuntimeError:
        return status

    status["generation"] = snapshot.generation
    if config_text is not None:
        status["checksum"] = config_checksum(config_text)

    if app_manager is not None:
        status["apps"] = dict(app_manager.app_statuses())

    if health_monitor is not None:
        for profile_id, health in health_monitor.statuses().items():
            profile = snapshot.profiles.get(profile_id)
            status["profiles"][profile_id] = {
                "state": health.state,
                "account_id": (
                    profile.expected_account_id if profile is not None else None
                ),
                "account_id_masked": health.account_id_masked,
                "last_sts_at": health.last_sts_at,
                "last_error": health.last_error,
                "consecutive_failures": health.consecutive_failures,
            }

    if task_registry is not None:
        status["tasks"] = {
            "total": task_registry.total_running(),
            "by_profile": task_registry.counts_by_profile(),
        }
    return status
