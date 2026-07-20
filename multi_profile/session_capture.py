from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .process_utils import terminate_process_tree


_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


class SessionCaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapturedSession:
    session_id: str
    process: Any


def parse_session_ids(text: str) -> set[str]:
    return {match.lower() for match in _UUID_RE.findall(text)}


class SessionCaptureCoordinator:
    def __init__(
        self,
        *,
        timeout: float = 30,
        poll_interval: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        terminate_process: Callable[[Any], None] = terminate_process_tree,
    ):
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._clock = clock
        self._sleep = sleep
        self._terminate_process = terminate_process
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, working_dir: str | Path) -> threading.Lock:
        key = str(Path(working_dir).resolve())
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def start_and_capture(
        self,
        working_dir: str | Path,
        *,
        list_session_ids: Callable[[], set[str]],
        start_process: Callable[[], Any],
    ) -> CapturedSession:
        with self._lock_for(working_dir):
            before = set(list_session_ids())
            process = start_process()
            deadline = self._clock() + self._timeout
            try:
                while True:
                    current = set(list_session_ids())
                    new_ids = current - before
                    if len(new_ids) == 1:
                        return CapturedSession(new_ids.pop(), process)
                    if len(new_ids) > 1:
                        raise SessionCaptureError("ambiguous new Kiro sessions")
                    if self._clock() >= deadline:
                        raise SessionCaptureError("Kiro session capture timed out")
                    self._sleep(self._poll_interval)
            except SessionCaptureError:
                self._terminate_process(process)
                raise
            except Exception as exc:
                self._terminate_process(process)
                raise SessionCaptureError("Kiro session listing failed") from exc
