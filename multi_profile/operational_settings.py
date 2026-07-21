from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class OperationalSettings:
    sts_timeout_sec: int = 10
    health_check_interval_sec: int = 600
    health_grace_sec: int = 1800
    health_jitter_max_sec: int = 60
    revision_keep: int = 20


_BOUNDS = {
    "AWS_STS_TIMEOUT_SEC": (3, 60, "sts_timeout_sec"),
    "PROFILE_HEALTH_CHECK_INTERVAL_SEC": (60, 3600, "health_check_interval_sec"),
    "PROFILE_HEALTH_GRACE_SEC": (0, 86400, "health_grace_sec"),
}


def _bounded(values: Mapping[str, str], key: str) -> int | None:
    raw = values.get(key, "").strip()
    if not raw:
        return None
    low, high, _ = _BOUNDS[key]
    try:
        number = int(raw)
    except ValueError:
        raise ValueError(f"{key} must be an integer between {low} and {high}") from None
    if not low <= number <= high:
        raise ValueError(f"{key} must be between {low} and {high}, got {number}")
    return number


def load_operational_settings(
    environ: Mapping[str, str] | None = None,
) -> OperationalSettings:
    values = environ if environ is not None else os.environ
    overrides = {}
    for key, (_, _, field) in _BOUNDS.items():
        number = _bounded(values, key)
        if number is not None:
            overrides[field] = number
    return OperationalSettings(**overrides)
