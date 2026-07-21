import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from multi_profile import (
    ConfigRegistry,
    SessionStore,
    TaskAlreadyRunning,
    TaskRegistry,
    TenantRouter,
)

APP_COUNT = 10
PROFILE_COUNT = 20
ROUTE_COUNT = 100
CONCURRENT_MESSAGES = 50


def build_yaml(tmp_path) -> str:
    apps = "\n".join(
        f"""  app-{i:02d}:
    app_id_env: FEISHU_APP_{i:02d}_ID
    app_secret_env: FEISHU_APP_{i:02d}_SECRET
    default_profile: profile-{i % PROFILE_COUNT:02d}"""
        for i in range(APP_COUNT)
    )
    profiles = "\n".join(
        f"""  profile-{i:02d}:
    aws_profile: aws-{i:02d}
    expected_account_id: "{100000000000 + i}"
    working_dir: {tmp_path}"""
        for i in range(PROFILE_COUNT)
    )
    routes = "\n".join(
        f"""  - app: app-{i % APP_COUNT:02d}
    chat_id: oc_chat_{i:03d}
    profile: profile-{i % PROFILE_COUNT:02d}"""
        for i in range(ROUTE_COUNT)
    )
    return f"version: 1\napps:\n{apps}\nprofiles:\n{profiles}\nroutes:\n{routes}\n"


def build_environ() -> dict:
    env = {}
    for i in range(APP_COUNT):
        env[f"FEISHU_APP_{i:02d}_ID"] = f"cli_{i:02d}"
        env[f"FEISHU_APP_{i:02d}_SECRET"] = f"secret_{i:02d}"
    return env


class FakeAdapter:
    """記錄每則訊息由哪個 App 收到與回覆，取代真實 FeishuAdapter。"""

    def __init__(self, app_key):
        self.app_key = app_key
        self.replies = []
        self._lock = threading.Lock()

    def reply(self, chat_id, user_id, text):
        with self._lock:
            self.replies.append((chat_id, user_id, text))


class FakeRuntime:
    """以 principal_key 向 TaskRegistry 保留任務並寫入 SessionStore，取代真實 Kiro 子程序。"""

    def __init__(self, tasks: TaskRegistry, sessions: SessionStore):
        self._tasks = tasks
        self._sessions = sessions

    def handle(self, context, delay=0.0):
        token = self._tasks.reserve(context.principal_key, context.profile_id)
        try:
            if delay:
                time.sleep(delay)
            self._sessions.register_new(
                context,
                f"uuid-{context.principal_key}",
                topic="scale-test",
            )
            return f"{context.app_key}|{context.profile_id}|{context.chat_id}"
        finally:
            self._tasks.finish(context.principal_key, token)


@pytest.fixture
def scale_env(tmp_path):
    config_file = tmp_path / "multi_profile_config.yaml"
    config_file.write_text(build_yaml(tmp_path), encoding="utf-8")
    registry = ConfigRegistry(config_file, environ=build_environ())
    registry.load_initial()
    tasks = TaskRegistry()
    sessions = SessionStore(tmp_path / "tenant_sessions.db")
    adapters = {f"app-{i:02d}": FakeAdapter(f"app-{i:02d}") for i in range(APP_COUNT)}
    runtime = FakeRuntime(tasks, sessions)
    return registry, tasks, sessions, adapters, runtime


def test_scale_routing_is_correct_for_all_routes(scale_env):
    registry, *_ = scale_env
    router = TenantRouter(registry.snapshot())

    for i in range(ROUTE_COUNT):
        app_key = f"app-{i % APP_COUNT:02d}"
        context = router.resolve(
            platform="feishu",
            app_key=app_key,
            chat_type="group",
            chat_id=f"oc_chat_{i:03d}",
            user_id="ou_user",
        )
        assert context.profile_id == f"profile-{i % PROFILE_COUNT:02d}"
        assert context.principal_key == f"feishu/{app_key}/group/oc_chat_{i:03d}/user/ou_user"


def test_fifty_concurrent_messages_have_no_shared_state_pollution(scale_env):
    registry, tasks, sessions, adapters, runtime = scale_env
    router = TenantRouter(registry.snapshot())

    def handle_message(i):
        app_key = f"app-{i % APP_COUNT:02d}"
        chat_id = f"oc_chat_{i:03d}"
        context = router.resolve(
            platform="feishu",
            app_key=app_key,
            chat_type="group",
            chat_id=chat_id,
            user_id=f"ou_user_{i % 7}",
        )
        result = runtime.handle(context)
        adapters[app_key].reply(chat_id, context.user_id, result)
        return context, result

    with ThreadPoolExecutor(max_workers=CONCURRENT_MESSAGES) as pool:
        outcomes = list(pool.map(handle_message, range(CONCURRENT_MESSAGES)))

    for i, (context, result) in enumerate(outcomes):
        expected_app = f"app-{i % APP_COUNT:02d}"
        assert result == f"{expected_app}|profile-{i % PROFILE_COUNT:02d}|oc_chat_{i:03d}"

    # 回覆全部來自原 App、原群
    total_replies = sum(len(a.replies) for a in adapters.values())
    assert total_replies == CONCURRENT_MESSAGES
    for app_key, adapter in adapters.items():
        for chat_id, _user, text in adapter.replies:
            assert text.startswith(f"{app_key}|")

    # Session 全部以各自的 principal_key 落庫，沒有互相覆寫
    principals = {context.principal_key for context, _ in outcomes}
    for context, _ in outcomes:
        record = sessions.resolve_active(context)
        assert record is not None
        assert record.principal_key == context.principal_key
        assert record.profile_id == context.profile_id
        assert record.kiro_session_id == f"uuid-{context.principal_key}"
    assert len(principals) == CONCURRENT_MESSAGES  # (app, chat, user) 全不同


def test_same_principal_second_task_rejected_under_load(scale_env):
    registry, tasks, _, _, runtime = scale_env
    router = TenantRouter(registry.snapshot())
    context = router.resolve(
        platform="feishu", app_key="app-00", chat_type="group",
        chat_id="oc_chat_000", user_id="ou_user_0",
    )

    token = tasks.reserve(context.principal_key, context.profile_id)
    try:
        with pytest.raises(TaskAlreadyRunning):
            runtime.handle(context)
    finally:
        tasks.finish(context.principal_key, token)


def test_hot_reload_does_not_block_in_flight_tasks(scale_env, tmp_path):
    registry, tasks, sessions, adapters, runtime = scale_env
    in_flight = []
    started = threading.Event()

    def slow_message(i):
        router = TenantRouter(registry.snapshot())  # 訊息開始時取一次 snapshot
        context = router.resolve(
            platform="feishu",
            app_key=f"app-{i % APP_COUNT:02d}",
            chat_type="group",
            chat_id=f"oc_chat_{i:03d}",
            user_id=f"ou_user_{i % 7}",
        )
        in_flight.append(context)
        if i == 0:
            started.set()
        return runtime.handle(context, delay=0.3)

    with ThreadPoolExecutor(max_workers=CONCURRENT_MESSAGES) as pool:
        futures = [pool.submit(slow_message, i) for i in range(CONCURRENT_MESSAGES)]
        assert started.wait(timeout=5)

        reload_start = time.monotonic()
        new_snapshot = registry.reload()  # 設定未變更，generation 仍應遞增
        reload_elapsed = time.monotonic() - reload_start

        results = [f.result(timeout=10) for f in futures]

    assert new_snapshot.generation == 2
    assert reload_elapsed < 1.0  # 熱載入不得被 50 個進行中任務阻塞
    assert all(in_flight_ctx.config_generation == 1 for in_flight_ctx in in_flight)
    assert len(results) == CONCURRENT_MESSAGES

    # 熱載入後的新訊息使用新 generation
    context = TenantRouter(registry.snapshot()).resolve(
        platform="feishu", app_key="app-00", chat_type="group",
        chat_id="oc_chat_000", user_id="ou_new",
    )
    assert context.config_generation == 2
