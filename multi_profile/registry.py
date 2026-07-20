from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from pathlib import Path

from .config_loader import load_config
from .models import ConfigSnapshot


class ConfigRegistry:
    def __init__(
        self,
        path: str | Path,
        *,
        environ: Mapping[str, str] | None = None,
    ):
        self._path = Path(path)
        self._environ = environ if environ is not None else os.environ
        self._snapshot: ConfigSnapshot | None = None
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def load_initial(self) -> ConfigSnapshot:
        candidate = load_config(self._path, environ=self._environ, generation=1)
        with self._lock:
            if self._snapshot is not None:
                raise RuntimeError("config registry is already loaded")
            self._snapshot = candidate
            return candidate

    def reload(self) -> ConfigSnapshot:
        with self._lock:
            current = self._snapshot
            if current is None:
                raise RuntimeError("config registry is not loaded")
            next_generation = current.generation + 1

        candidate = load_config(
            self._path,
            environ=self._environ,
            generation=next_generation,
        )
        with self._lock:
            current = self._snapshot
            if current is None:
                raise RuntimeError("config registry is not loaded")
            if current.generation >= candidate.generation:
                candidate = load_config(
                    self._path,
                    environ=self._environ,
                    generation=current.generation + 1,
                )
            self._snapshot = candidate
            return candidate

    def snapshot(self) -> ConfigSnapshot:
        with self._lock:
            if self._snapshot is None:
                raise RuntimeError("config registry is not loaded")
            return self._snapshot
