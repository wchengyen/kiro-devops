from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable


class TaskAlreadyRunning(RuntimeError):
    pass


@dataclass(frozen=True)
class CancellationHandle:
    token: str
    process: Any


@dataclass
class _RunningTask:
    token: str
    profile_id: str
    started_at: float
    process: Any = None
    cancel_requested: bool = False


class TaskRegistry:
    def __init__(self, *, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._tasks: dict[str, _RunningTask] = {}
        self._lock = threading.Lock()

    def reserve(self, principal_key: str, profile_id: str) -> str:
        with self._lock:
            if principal_key in self._tasks:
                raise TaskAlreadyRunning(principal_key)
            token = uuid.uuid4().hex
            self._tasks[principal_key] = _RunningTask(
                token=token,
                profile_id=profile_id,
                started_at=self._clock(),
            )
            return token

    def attach(self, principal_key: str, token: str, process: Any) -> bool:
        with self._lock:
            task = self._tasks.get(principal_key)
            if task is None or task.token != token:
                raise RuntimeError("task reservation no longer exists")
            task.process = process
            return task.cancel_requested

    def finish(self, principal_key: str, token: str) -> None:
        with self._lock:
            task = self._tasks.get(principal_key)
            if task is not None and task.token == token:
                self._tasks.pop(principal_key, None)

    def claim_completion(self, principal_key: str, token: str) -> bool:
        with self._lock:
            task = self._tasks.get(principal_key)
            if task is None or task.token != token or task.cancel_requested:
                return False
            self._tasks.pop(principal_key, None)
            return True

    def should_cancel(self, principal_key: str, token: str) -> bool:
        with self._lock:
            task = self._tasks.get(principal_key)
            return task is None or task.token != token or task.cancel_requested

    def is_busy(self, principal_key: str) -> bool:
        with self._lock:
            return principal_key in self._tasks

    def status(self, principal_key: str) -> str | None:
        with self._lock:
            task = self._tasks.get(principal_key)
            if task is None:
                return None
            elapsed = max(0, int(self._clock() - task.started_at))
            state = "cancelling" if task.cancel_requested else "running"
            return f"{task.profile_id} task {state} ({elapsed}s)"

    def request_cancel(self, principal_key: str) -> CancellationHandle | None:
        with self._lock:
            task = self._tasks.get(principal_key)
            if task is None:
                return None
            task.cancel_requested = True
            return CancellationHandle(task.token, task.process)
