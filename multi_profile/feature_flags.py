from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


_TRUE_VALUES = {"true", "1", "yes"}


def is_enabled(environ: Mapping[str, str] | None = None) -> bool:
    values = environ if environ is not None else os.environ
    return values.get("MULTI_PROFILE_ENABLED", "false").strip().lower() in _TRUE_VALUES


def config_path(
    environ: Mapping[str, str] | None = None,
    *,
    project_dir: str | Path,
) -> Path:
    values = environ if environ is not None else os.environ
    configured = values.get("MULTI_PROFILE_CONFIG", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(project_dir) / "multi_profile_config.yaml"
