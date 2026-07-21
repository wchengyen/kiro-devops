from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .models import ConfigSnapshot, ProfileConfig
from .operational_settings import OperationalSettings
from .sts import StsResult, mask_account_id, run_sts_check


STATE_ACTIVE = "active"
STATE_DEGRADED = "degraded"
STATE_BLOCKED = "blocked"
STATE_DISABLED = "disabled"


class ProfileUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ProfileHealth:
    profile_id: str
    state: str
    account_id_masked: str | None
    last_sts_at: float | None
    last_error: str | None
    consecutive_failures: int


@dataclass
class _HealthState:
    state: str = STATE_ACTIVE
    account_id_masked: str | None = None
    last_sts_at: float | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    first_failure_at: float | None = None
    last_success_at: float | None = None


class ProfileHealthMonitor:
    """週期性 STS 健康檢查。只回報狀態，永不自動切換 profile（規格 §14）。"""

    def __init__(
        self,
        snapshot_getter: Callable[[], ConfigSnapshot],
        *,
        settings: OperationalSettings | None = None,
        clock: Callable[[], float] = time.time,
        sts_runner: Callable[..., StsResult] = run_sts_check,
        jitter: Callable[[float], float] = lambda high: random.uniform(0, high),
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ):
        self._snapshot_getter = snapshot_getter
        self._settings = settings or OperationalSettings()
        self._clock = clock
        self._sts_runner = sts_runner
        self._jitter = jitter
        self._thread_factory = thread_factory
        self._states: dict[str, _HealthState] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- 檢查邏輯 -------------------------------------------------

    def check_all_now(self) -> None:
        snapshot = self._snapshot_getter()
        for profile in snapshot.profiles.values():
            self._check_one(profile)

    def _check_one(self, profile: ProfileConfig) -> None:
        if not profile.enabled:
            with self._lock:
                self._states[profile.profile_id] = _HealthState(state=STATE_DISABLED)
            return

        result = self._sts_runner(
            profile, timeout_sec=self._settings.sts_timeout_sec,
        )
        now = self._clock()
        with self._lock:
            state = self._states.setdefault(profile.profile_id, _HealthState())
            if result.ok and result.account_id == profile.expected_account_id:
                state.state = STATE_ACTIVE
                state.account_id_masked = mask_account_id(result.account_id)
                state.last_sts_at = now
                state.last_error = None
                state.consecutive_failures = 0
                state.first_failure_at = None
                state.last_success_at = now
                return

            state.last_sts_at = now
            if result.ok:
                # Account ID 不符：立即 blocked，不適用 grace（規格 §14）
                state.state = STATE_BLOCKED
                state.last_error = "account_mismatch"
                state.account_id_masked = (
                    mask_account_id(result.account_id) if result.account_id else None
                )
            elif result.error_kind == "profile_not_found":
                state.state = STATE_BLOCKED
                state.last_error = "profile_not_found"
            else:
                # 暫時性失敗：grace 內 degraded，超過 grace blocked。
                # grace 自最近一次 STS 成功起算（從未成功則自首次失敗起算）。
                state.consecutive_failures += 1
                if state.first_failure_at is None:
                    state.first_failure_at = now
                reference = (
                    state.last_success_at
                    if state.last_success_at is not None
                    else state.first_failure_at
                )
                grace = self._settings.health_grace_sec
                if now - reference > grace:
                    state.state = STATE_BLOCKED
                else:
                    state.state = STATE_DEGRADED
                state.last_error = result.error_kind

    # ---- 查詢與閘門 -----------------------------------------------

    def health(self, profile_id: str) -> ProfileHealth:
        with self._lock:
            state = self._states.get(profile_id)
            if state is None:
                # 尚未檢查過：樂觀視為 active，由下一輪檢查修正
                return ProfileHealth(profile_id, STATE_ACTIVE, None, None, None, 0)
            return ProfileHealth(
                profile_id=profile_id,
                state=state.state,
                account_id_masked=state.account_id_masked,
                last_sts_at=state.last_sts_at,
                last_error=state.last_error,
                consecutive_failures=state.consecutive_failures,
            )

    def statuses(self) -> dict[str, ProfileHealth]:
        snapshot = self._snapshot_getter()
        return {pid: self.health(pid) for pid in snapshot.profiles}

    def ensure_usable(self, profile_id: str) -> None:
        """新任務閘門：blocked/disabled/未知 profile 一律拒絕，不提供替代。"""
        snapshot = self._snapshot_getter()
        profile = snapshot.profiles.get(profile_id)
        if profile is None:
            raise ProfileUnavailable(f"unknown profile: {profile_id}")
        health = self.health(profile_id)
        if health.state == STATE_DISABLED:
            raise ProfileUnavailable(f"profile is disabled: {profile_id}")
        if health.state == STATE_BLOCKED:
            raise ProfileUnavailable(
                f"profile is blocked: {profile_id} ({health.last_error})"
            )

    def on_config_reload(self, snapshot: ConfigSnapshot) -> None:
        """熱載入後對齊 profile 集合：移除已刪除者，新增者下輪檢查。

        同時把快照來源切到最新 snapshot（生產中等價於 registry 已切換的
        snapshot；測試中讓固定 getter 的 monitor 也能看到新集合）。
        """
        with self._lock:
            self._snapshot_getter = lambda: snapshot
            for pid in list(self._states):
                if pid not in snapshot.profiles:
                    del self._states[pid]

    # ---- 背景排程 -------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = self._thread_factory(
            target=self._run_loop,
            name="profile-health-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.check_all_now()
            except Exception:
                # 單輪失敗不得終止監控執行緒；狀態保留至下輪
                pass
            delay = (
                self._settings.health_check_interval_sec
                + self._jitter(self._settings.health_jitter_max_sec)
            )
            self._stop_event.wait(delay)
