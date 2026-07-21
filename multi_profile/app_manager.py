from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from enum import Enum

from .models import AppConfig, ConfigSnapshot
from .poll_sets import poll_chat_ids_for_app
from .registry import ConfigRegistry

log = logging.getLogger("multi-profile-app-manager")

BACKOFF_SCHEDULE: tuple[int, ...] = (1, 2, 4, 8, 16, 32, 60)


class AppConnState(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    PENDING_RESTART = "pending-restart"


class _SupervisedApp:
    """單一 App 的監督狀態；只記錄 app_key 與 env 名稱，永不記錄 Secret 值。"""

    def __init__(self, app_config: AppConfig):
        self.app_config = app_config
        self.adapter = None
        self.state = AppConnState.DISCONNECTED
        self.attempts = 0
        self.last_error: str | None = None
        self.thread: threading.Thread | None = None

    def status(self) -> dict:
        poll_errors = getattr(self.adapter, "poll_errors", {}) if self.adapter else {}
        return {
            "state": self.state.value,
            "reconnect_attempts": self.attempts,
            "last_error": self.last_error,
            "poll_errors": dict(poll_errors),
        }


class AppManager:
    """依設定 snapshot 建立與監督多個飛書 Adapter；單 App 故障不影響其他 App。"""

    def __init__(
        self,
        *,
        registry: ConfigRegistry,
        adapter_factory: Callable[..., object],
        dispatcher,
        on_message: Callable,
        environ: Mapping[str, str] | None = None,
        backoff_schedule: tuple[int, ...] = BACKOFF_SCHEDULE,
        stable_after: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ):
        self._registry = registry
        self._adapter_factory = adapter_factory
        self._dispatcher = dispatcher
        self._on_message = on_message
        self._environ = environ if environ is not None else os.environ
        self._backoff_schedule = backoff_schedule
        self._stable_after = stable_after
        self._sleep = sleep
        self._clock = clock
        self._thread_factory = thread_factory
        self._apps: dict[str, _SupervisedApp] = {}
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

    # ---- 退避計算（純函式，便於測試） ----

    def _backoff_for_attempt(self, attempt: int) -> int:
        index = min(attempt, len(self._backoff_schedule) - 1)
        return self._backoff_schedule[index]

    def _next_attempt(self, *, previous_attempt: int, uptime_seconds: float) -> int:
        if uptime_seconds >= self._stable_after:
            return 0
        return previous_attempt + 1

    # ---- 生命週期 ----

    def start_all(self) -> None:
        snapshot = self._registry.snapshot()
        for app_key, app_config in snapshot.apps.items():
            supervised = _SupervisedApp(app_config)
            with self._lock:
                self._apps[app_key] = supervised
            if not app_config.enabled:
                log.info(f"App {app_key} 已停用，不建立連線")
                continue
            self._launch(supervised)

    def stop_all(self) -> None:
        self._stop_event.set()
        with self._lock:
            apps = list(self._apps.values())
        for supervised in apps:
            adapter = supervised.adapter
            if adapter is not None and hasattr(adapter, "stop"):
                try:
                    adapter.stop()
                except Exception:
                    log.exception(f"停止 App {supervised.app_config.app_key} 失敗")
            supervised.state = AppConnState.DISCONNECTED
        for supervised in apps:
            thread = supervised.thread
            if thread is not None:
                thread.join(timeout=5)

    def status(self) -> dict[str, dict]:
        with self._lock:
            return {key: app.status() for key, app in self._apps.items()}

    # ---- 熱載入（計畫 4 的 Dashboard 會呼叫；本計畫只標記 pending-restart） ----

    def on_snapshot_changed(self, new_snapshot: ConfigSnapshot) -> None:
        with self._lock:
            for app_key, new_config in new_snapshot.apps.items():
                supervised = self._apps.get(app_key)
                if supervised is None:
                    # 新 App：需重啟才連線；未運行的 App 不得接收有效路由
                    pending = _SupervisedApp(new_config)
                    pending.state = AppConnState.PENDING_RESTART
                    self._apps[app_key] = pending
                    continue
                if self._connection_fields(supervised.app_config) != self._connection_fields(new_config):
                    supervised.state = AppConnState.PENDING_RESTART
                supervised.app_config = new_config
            for removed in set(self._apps) - set(new_snapshot.apps):
                supervised = self._apps[removed]
                if supervised.state is not AppConnState.DISCONNECTED:
                    supervised.state = AppConnState.PENDING_RESTART

    @staticmethod
    def _connection_fields(config: AppConfig) -> tuple:
        return (config.enabled, config.app_id_env, config.app_secret_env)

    # ---- 內部 ----

    def _build_adapter(self, supervised: _SupervisedApp):
        config = supervised.app_config
        app_id = self._environ.get(config.app_id_env, "").strip()
        app_secret = self._environ.get(config.app_secret_env, "").strip()
        if not app_id or not app_secret:
            raise RuntimeError(
                f"App {config.app_key} 的 env {config.app_id_env}/{config.app_secret_env} 無有效值"
            )
        return self._adapter_factory(
            app_key=config.app_key,
            app_id=app_id,
            app_secret=app_secret,
            on_message=self._on_message,
            poll_chat_ids=lambda: poll_chat_ids_for_app(
                self._registry.snapshot(), config.app_key
            ),
            group_alert_listen=True,
            on_disconnected=None,  # 由監督迴圈以 start() 返回偵測
        )

    def _launch(self, supervised: _SupervisedApp) -> None:
        try:
            adapter = self._build_adapter(supervised)
        except Exception as e:
            supervised.state = AppConnState.DISCONNECTED
            supervised.last_error = f"{type(e).__name__}: {e}"
            log.error(f"建立 App {supervised.app_config.app_key} 失敗: {supervised.last_error}")
            return
        supervised.adapter = adapter
        self._dispatcher.register(adapter, app_key=supervised.app_config.app_key)
        thread = self._thread_factory(
            target=self._supervise,
            args=(supervised,),
            daemon=True,
            name=f"app-supervise-{supervised.app_config.app_key}",
        )
        supervised.thread = thread
        thread.start()
        log.info(f"App {supervised.app_config.app_key} 監督執行緒已啟動")

    def _supervise(self, supervised: _SupervisedApp) -> None:
        app_key = supervised.app_config.app_key
        while not self._stop_event.is_set():
            supervised.state = AppConnState.CONNECTED
            started_at = self._clock()
            try:
                supervised.adapter.start()
                supervised.last_error = None
            except Exception as e:
                supervised.last_error = f"{type(e).__name__}: {e}"
                # 連線已中斷：立刻標記，避免異常日誌期間狀態仍顯示 connected
                supervised.state = AppConnState.RECONNECTING
                log.exception(f"App {app_key} 連線結束（異常）")
            if self._stop_event.is_set():
                break

            uptime = self._clock() - started_at
            # 先以目前 attempts 決定退避（首次崩潰 attempts=0 → 1s），再依穩定度更新
            delay = self._backoff_for_attempt(supervised.attempts)
            supervised.attempts = self._next_attempt(
                previous_attempt=supervised.attempts, uptime_seconds=uptime
            )
            supervised.state = AppConnState.RECONNECTING
            log.warning(
                f"App {app_key} 將於 {delay}s 後重連（第 {supervised.attempts + 1} 次）"
            )
            self._sleep(delay)
            if self._stop_event.is_set():
                break
            try:
                # lark WS client 不可重啟；以 factory 重建整個 Adapter
                supervised.adapter = self._build_adapter(supervised)
                self._dispatcher.register(supervised.adapter, app_key=app_key)
            except Exception as e:
                supervised.last_error = f"{type(e).__name__}: {e}"
                supervised.attempts += 1
                self._sleep(self._backoff_for_attempt(supervised.attempts))
        supervised.state = AppConnState.DISCONNECTED
