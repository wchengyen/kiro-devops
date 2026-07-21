import logging
import threading
import time

import pytest

from multi_profile.app_manager import AppConnState, AppManager
from multi_profile.models import AppConfig, ProfileConfig, RouteConfig, create_snapshot
from platform_dispatcher import PlatformDispatcher


class FakeFeishuAdapter:
    """可控制的假 Adapter：start() 阻塞直到 stop() 或 crash_after 秒。"""

    instances = []

    def __init__(self, *, app_key, app_id, app_secret, on_message,
                 poll_chat_ids=None, group_alert_listen=None,
                 on_disconnected=None, crash_after=None):
        self.app_key = app_key
        self.app_id = app_id
        self.app_secret = app_secret
        self.on_message = on_message
        self.poll_chat_ids = poll_chat_ids
        self.group_alert_listen = group_alert_listen
        self.on_disconnected = on_disconnected
        self.crash_after = crash_after
        self.started = threading.Event()
        self.stopped = False
        self.poll_errors = {}
        FakeFeishuAdapter.instances.append(self)

    @property
    def platform(self):
        return "feishu"

    def start(self):
        self.started.set()
        if self.crash_after is not None:
            time.sleep(self.crash_after)
            exc = RuntimeError(f"{self.app_key} ws crashed")
            if self.on_disconnected:
                self.on_disconnected(exc)
            return
        while not self.stopped:
            time.sleep(0.01)

    def stop(self):
        self.stopped = True


class FakeRegistry:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


def make_snapshot(apps, routes=()):
    profiles = {
        "prod-cn": ProfileConfig(
            profile_id="prod-cn",
            aws_profile="production",
            expected_account_id="123456789012",
            working_dir="/tmp",
        )
    }
    return create_snapshot(1, apps, profiles, tuple(routes))


def make_app(key, enabled=True, default_profile="prod-cn"):
    env_prefix = key.upper().replace("-", "_")
    return AppConfig(
        app_key=key,
        enabled=enabled,
        app_id_env=f"{env_prefix}_APP_ID",
        app_secret_env=f"{env_prefix}_APP_SECRET",
        default_profile=default_profile,
    )


def make_manager(snapshot, *, factory=None, sleeps=None, clock=None, environ=None):
    FakeFeishuAdapter.instances = []
    recorded_sleeps = sleeps if sleeps is not None else []
    dispatcher = PlatformDispatcher()
    manager = AppManager(
        registry=FakeRegistry(snapshot),
        adapter_factory=factory or (lambda **kw: FakeFeishuAdapter(**kw)),
        dispatcher=dispatcher,
        on_message=lambda msg: None,
        environ=environ if environ is not None else {
            "APP_A_APP_ID": "cli_a", "APP_A_APP_SECRET": "secret_a_value",
            "APP_B_APP_ID": "cli_b", "APP_B_APP_SECRET": "secret_b_value",
        },
        sleep=lambda sec: recorded_sleeps.append(sec),
        clock=clock or time.monotonic,
    )
    return manager, dispatcher, recorded_sleeps


def test_builds_adapter_per_enabled_app_and_registers_by_app_key():
    snapshot = make_snapshot({"app-a": make_app("app-a"), "app-b": make_app("app-b")})
    manager, dispatcher, _ = make_manager(snapshot)

    manager.start_all()

    assert dispatcher.get_adapter("feishu", "app-a") is not None
    assert dispatcher.get_adapter("feishu", "app-b") is not None
    assert manager.status()["app-a"]["state"] == AppConnState.CONNECTED.value
    assert manager.status()["app-b"]["state"] == AppConnState.CONNECTED.value
    manager.stop_all()


def test_disabled_app_is_not_built():
    snapshot = make_snapshot({
        "app-a": make_app("app-a"),
        "app-b": make_app("app-b", enabled=False),
    })
    manager, dispatcher, _ = make_manager(snapshot)

    manager.start_all()

    assert dispatcher.get_adapter("feishu", "app-b") is None
    assert manager.status()["app-b"]["state"] == AppConnState.DISCONNECTED.value
    manager.stop_all()


def test_secrets_resolved_from_environ_names_and_never_logged(caplog):
    snapshot = make_snapshot({"app-a": make_app("app-a")})
    manager, _, _ = make_manager(snapshot)

    with caplog.at_level(logging.DEBUG):
        manager.start_all()

    adapter = FakeFeishuAdapter.instances[0]
    assert adapter.app_id == "cli_a"
    assert adapter.app_secret == "secret_a_value"
    assert "secret_a_value" not in caplog.text
    manager.stop_all()


def test_poll_chat_ids_provider_reads_current_snapshot():
    routes = (RouteConfig("app-a", "oc_1", "prod-cn", poll_alerts=True),)
    snapshot = make_snapshot({"app-a": make_app("app-a")}, routes)
    manager, _, _ = make_manager(snapshot)

    manager.start_all()

    provider = FakeFeishuAdapter.instances[0].poll_chat_ids
    assert callable(provider)
    assert provider() == ["oc_1"]
    manager.stop_all()


def test_crash_triggers_reconnect_with_exponential_backoff():
    snapshot = make_snapshot({"app-a": make_app("app-a")})
    manager, _, sleeps = make_manager(snapshot)

    crash_budget = {"count": 0}

    def crashing_factory(**kw):
        crash_budget["count"] += 1
        # 前 3 次立即崩潰，之後穩定
        return FakeFeishuAdapter(crash_after=0 if crash_budget["count"] <= 3 else None, **kw)

    manager._adapter_factory = crashing_factory
    manager._stable_after = 9999  # 不重置，專注觀察退避序列

    manager.start_all()
    deadline = time.time() + 5
    while crash_budget["count"] < 4 and time.time() < deadline:
        time.time() and __import__("time").sleep(0.01)
    # stop_all 會把狀態收斂為 disconnected，需在停止前取狀態
    state_before_stop = manager.status()["app-a"]["state"]
    manager.stop_all()

    assert sleeps[:3] == [1, 2, 4]
    assert state_before_stop in (
        AppConnState.CONNECTED.value, AppConnState.RECONNECTING.value
    )


def test_backoff_is_capped_at_sixty_seconds():
    manager, _, _ = make_manager(make_snapshot({"app-a": make_app("app-a")}))
    assert manager._backoff_for_attempt(0) == 1
    assert manager._backoff_for_attempt(1) == 2
    assert manager._backoff_for_attempt(5) == 32
    assert manager._backoff_for_attempt(6) == 60
    assert manager._backoff_for_attempt(20) == 60


def test_stable_connection_resets_backoff():
    manager, _, _ = make_manager(make_snapshot({"app-a": make_app("app-a")}))
    assert manager._next_attempt(previous_attempt=5, uptime_seconds=60.0) == 0
    assert manager._next_attempt(previous_attempt=5, uptime_seconds=1.0) == 6


def test_one_app_crash_loop_does_not_affect_other_app():
    snapshot = make_snapshot({"app-a": make_app("app-a"), "app-b": make_app("app-b")})
    manager, dispatcher, _ = make_manager(snapshot)

    def factory(**kw):
        if kw["app_key"] == "app-a":
            return FakeFeishuAdapter(crash_after=0, **kw)
        return FakeFeishuAdapter(**kw)

    manager._adapter_factory = factory
    manager.start_all()

    try:
        app_b = dispatcher.get_adapter("feishu", "app-b")
        assert app_b is not None and app_b.started.is_set()
        # 監督迴圈在 adapter.start() 執行期間會短暫標記 connected，單次取樣有競態；
        # 在視窗內連續取樣：app-b 全程 connected（故障隔離），app-a 進入過退避重連
        app_b_states, app_a_states = set(), set()
        deadline = time.time() + 0.5
        while time.time() < deadline:
            status = manager.status()
            app_b_states.add(status["app-b"]["state"])
            app_a_states.add(status["app-a"]["state"])
            time.sleep(0.01)
        assert app_b_states == {AppConnState.CONNECTED.value}
        assert app_a_states & {
            AppConnState.RECONNECTING.value, AppConnState.DISCONNECTED.value
        }
    finally:
        manager.stop_all()
    assert app_b.stopped is True


def test_snapshot_change_marks_connection_changes_pending_restart():
    snapshot_v1 = make_snapshot({"app-a": make_app("app-a")})
    manager, _, _ = make_manager(snapshot_v1)
    manager.start_all()

    # 變更憑證引用 → pending-restart；純路由變更 → 不標記
    changed = make_app("app-a")
    changed = AppConfig(
        app_key="app-a",
        app_id_env="NEW_APP_ID",
        app_secret_env="APP_A_APP_SECRET",
        default_profile="prod-cn",
    )
    snapshot_v2 = make_snapshot({"app-a": changed})
    manager.on_snapshot_changed(snapshot_v2)

    assert manager.status()["app-a"]["state"] == AppConnState.PENDING_RESTART.value
    manager.stop_all()


def test_route_only_snapshot_change_does_not_require_restart():
    snapshot_v1 = make_snapshot({"app-a": make_app("app-a")})
    manager, _, _ = make_manager(snapshot_v1)
    manager.start_all()

    snapshot_v2 = make_snapshot(
        {"app-a": make_app("app-a")},
        (RouteConfig("app-a", "oc_new", "prod-cn", poll_alerts=True),),
    )
    manager.on_snapshot_changed(snapshot_v2)

    assert manager.status()["app-a"]["state"] == AppConnState.CONNECTED.value
    manager.stop_all()


def test_new_app_in_snapshot_is_pending_restart_not_auto_connected():
    snapshot_v1 = make_snapshot({"app-a": make_app("app-a")})
    manager, dispatcher, _ = make_manager(snapshot_v1)
    manager.start_all()

    snapshot_v2 = make_snapshot({"app-a": make_app("app-a"), "app-b": make_app("app-b")})
    manager.on_snapshot_changed(snapshot_v2)

    assert dispatcher.get_adapter("feishu", "app-b") is None
    assert manager.status()["app-b"]["state"] == AppConnState.PENDING_RESTART.value
    manager.stop_all()
