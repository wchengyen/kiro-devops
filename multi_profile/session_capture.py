from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


class SessionCaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionBaseline:
    """begin() 拍攝的啟動前 Session UUID 快照，供程序退出後 capture() 比對。"""

    session_ids: frozenset[str]


def parse_session_ids(text: str) -> set[str]:
    return {match.lower() for match in _UUID_RE.findall(text)}


class SessionCaptureCoordinator:
    """capture-at-exit：kiro-cli 只在 chat 程序「退出時」才把 conversation 寫入
    sqlite（2.4.1 實測，row 落盤可能再延遲數秒），因此無法在程序運行中捕捉。

    - begin()：啟動前在 per-working-dir 鎖內拍攝 baseline（短臨界區）。
    - capture()：程序退出後短暫輪詢，取 new = current − baseline − claimed。
      恰好 1 個 → claim 並回傳；0 個（逾時）→ not persisted；>1 個 → ambiguous。
      歧義一律 fail closed，絕不猜測。
    - claimed 集合讓同目錄並行的新 chat 在一般情況下各自綁定正確 session；
      殘餘競態（同一輪詢窗口同時落盤且皆未 claim）會落入歧義分支，同樣正確。
    鎖只保護 baseline／claim 臨界區，不涵蓋 chat 執行期間。
    """

    def __init__(
        self,
        *,
        timeout: float = 30,
        poll_interval: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._clock = clock
        self._sleep = sleep
        self._locks: dict[str, threading.Lock] = {}
        self._claimed: dict[str, set[str]] = {}
        self._locks_guard = threading.Lock()

    @staticmethod
    def _key(working_dir: str | Path) -> str:
        return str(Path(working_dir).resolve())

    def _lock_for(self, working_dir: str | Path) -> threading.Lock:
        key = self._key(working_dir)
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _claimed_for(self, working_dir: str | Path) -> set[str]:
        """回傳該工作目錄的已認領集合；只能在持有 per-dir 鎖時使用。"""
        key = self._key(working_dir)
        with self._locks_guard:
            return self._claimed.setdefault(key, set())

    def begin(
        self,
        working_dir: str | Path,
        *,
        list_session_ids: Callable[[], set[str]],
    ) -> SessionBaseline:
        with self._lock_for(working_dir):
            return SessionBaseline(frozenset(list_session_ids()))

    def capture(
        self,
        working_dir: str | Path,
        baseline: SessionBaseline,
        *,
        list_session_ids: Callable[[], set[str]],
    ) -> str:
        deadline = self._clock() + self._timeout
        while True:
            try:
                current = set(list_session_ids())
            except Exception as exc:
                raise SessionCaptureError("Kiro session listing failed") from exc
            with self._lock_for(working_dir):
                claimed = self._claimed_for(working_dir)
                new_ids = current - baseline.session_ids - claimed
                if len(new_ids) == 1:
                    session_id = new_ids.pop()
                    claimed.add(session_id)
                    return session_id
                if len(new_ids) > 1:
                    raise SessionCaptureError("ambiguous new Kiro sessions")
            if self._clock() >= deadline:
                raise SessionCaptureError("Kiro session not persisted after exit")
            self._sleep(self._poll_interval)
