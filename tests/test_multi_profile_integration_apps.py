import time

import pytest

from adapters.base import IncomingMessage
from multi_profile import (
    AppConnState,
    AppManager,
    ConfigRegistry,
    GroupAlertRunner,
    MultiProfilePipeline,
)
from platform_dispatcher import PlatformDispatcher


CONFIG = """
version: 1
apps:
  app-a:
    app_id_env: APP_A_APP_ID
    app_secret_env: APP_A_APP_SECRET
    default_profile: prod-cn
  app-b:
    app_id_env: APP_B_APP_ID
    app_secret_env: APP_B_APP_SECRET
    default_profile: prod-us
profiles:
  prod-cn:
    aws_profile: production-cn
    aws_region: cn-northwest-1
    expected_account_id: "123456789012"
    working_dir: {wd}
  prod-us:
    aws_profile: production-us
    aws_region: us-east-1
    expected_account_id: "210987654321"
    working_dir: {wd}
routes:
  - app: app-a
    chat_id: oc_shared
    profile: prod-cn
    poll_alerts: true
  - app: app-b
    chat_id: oc_shared
    profile: prod-us
    poll_alerts: true
"""

ENVIRON = {
    "APP_A_APP_ID": "cli_a", "APP_A_APP_SECRET": "sa",
    "APP_B_APP_ID": "cli_b", "APP_B_APP_SECRET": "sb",
}


class FakeFeishuAdapter:
    platform = "feishu"

    def __init__(self, *, app_key, crash_after=None, **kw):
        self.app_key = app_key
        self.kw = kw
        self.replies = []
        self.sent = []
        self.poll_errors = {}
        self._crash_after = crash_after
        self._stopped = False

    def start(self):
        if self._crash_after is not None:
            time.sleep(self._crash_after)
            raise RuntimeError(f"{self.app_key} crashed")
        while not self._stopped:
            time.sleep(0.01)

    def stop(self):
        self._stopped = True

    def reply(self, incoming, payload):
        self.replies.append((incoming, payload))


class FakeRuntime:
    def __init__(self):
        self.executed = []

    def is_busy(self, context):
        return False

    def status(self, context):
        return None

    def cancel(self, context):
        return False

    def execute(self, context, prompt, **callbacks):
        self.executed.append((context, prompt))


class FakeSessionStore:
    def clear_active(self, principal_key):
        pass


ALERT_TEXT = "告警名称：CPUHigh\n告警级别：critical"


def parse_alert(text):
    if "告警名称" in text:
        return {"title": "CPUHigh", "severity": "critical"}
    return None


def group_message(app_key, chat_id, text, is_at=True, user="ou_1"):
    return IncomingMessage(
        platform="feishu",
        app_key=app_key,
        raw_user_id=user,
        unified_user_id=f"feishu:{user}",
        message_id=f"m-{app_key}-{chat_id}-{text[:6]}",
        text=text,
        chat_type="group",
        is_at_me=is_at,
        group_id=chat_id,
    )


@pytest.fixture
def stack(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG.format(wd=tmp_path), encoding="utf-8")
    registry = ConfigRegistry(config, environ=ENVIRON)
    registry.load_initial()
    dispatcher = PlatformDispatcher()
    runtime = FakeRuntime()
    adapters = {}

    def factory(**kw):
        adapter = FakeFeishuAdapter(**kw)
        adapters[kw["app_key"]] = adapter
        return adapter

    manager = AppManager(
        registry=registry,
        adapter_factory=factory,
        dispatcher=dispatcher,
        on_message=lambda msg: None,
        environ=ENVIRON,
    )
    manager.start_all()

    analysis_calls = []

    def fake_analysis(record, context):
        analysis_calls.append((record, context))
        return ("分析結果", "agent")

    alert_runner = GroupAlertRunner(dispatcher=dispatcher, run_analysis=fake_analysis)
    pipeline = MultiProfilePipeline(
        registry=registry,
        dispatcher=dispatcher,
        runtime=runtime,
        session_store=FakeSessionStore(),
        alert_runner=alert_runner,
        parse_alert=parse_alert,
        thread_factory=lambda **kw: _InlineThread(**kw),
    )
    yield pipeline, dispatcher, manager, adapters, runtime, analysis_calls
    manager.stop_all()


class _InlineThread:
    def __init__(self, target, args=(), **kw):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


def test_both_adapters_registered_under_own_app_key(stack):
    _, dispatcher, _, adapters, _, _ = stack
    assert dispatcher.get_adapter("feishu", "app-a") is adapters["app-a"]
    assert dispatcher.get_adapter("feishu", "app-b") is adapters["app-b"]


def test_reply_goes_through_originating_app(stack):
    pipeline, _, _, adapters, runtime, _ = stack
    pipeline.handle(group_message("app-a", "oc_shared", "hello"))
    pipeline.handle(group_message("app-b", "oc_shared", "hello"))

    assert len(adapters["app-a"].replies) == 1
    assert len(adapters["app-b"].replies) == 1
    ctx_a, _ = runtime.executed[0]
    ctx_b, _ = runtime.executed[1]
    assert ctx_a.profile_id == "prod-cn"
    assert ctx_b.profile_id == "prod-us"


def test_same_chat_id_across_apps_does_not_collide(stack):
    pipeline, _, _, _, runtime, _ = stack
    pipeline.handle(group_message("app-a", "oc_shared", "hi", user="ou_same"))
    pipeline.handle(group_message("app-b", "oc_shared", "hi", user="ou_same"))

    principals = {ctx.principal_key for ctx, _ in runtime.executed}
    assert principals == {
        "feishu/app-a/group/oc_shared/user/ou_same",
        "feishu/app-b/group/oc_shared/user/ou_same",
    }


def test_alert_uses_route_profile_and_replies_via_original_app(stack):
    pipeline, _, _, adapters, _, analysis_calls = stack
    pipeline.handle(group_message("app-b", "oc_shared", ALERT_TEXT, is_at=False))

    assert len(analysis_calls) == 1
    _, context = analysis_calls[0]
    assert context.profile.aws_profile == "production-us"
    assert context.profile.aws_region == "us-east-1"
    # 回覆（分析中提示 + 分析結果）全部經 app-b，app-a 無任何訊息
    assert len(adapters["app-b"].replies) == 2
    assert adapters["app-a"].replies == []


def test_unmapped_group_on_one_app_does_not_affect_other(stack):
    pipeline, _, _, adapters, runtime, _ = stack
    pipeline.handle(group_message("app-a", "oc_unmapped", "hello"))
    pipeline.handle(group_message("app-b", "oc_shared", "hello"))

    assert "未配置" in adapters["app-a"].replies[0][1].text
    assert len(runtime.executed) == 1  # 只有 app-b 的訊息進入執行
    assert runtime.executed[0][0].app_key == "app-b"


def test_single_app_crash_does_not_stop_other_app(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG.format(wd=tmp_path), encoding="utf-8")
    registry = ConfigRegistry(config, environ=ENVIRON)
    registry.load_initial()
    dispatcher = PlatformDispatcher()
    sleeps = []

    def factory(**kw):
        if kw["app_key"] == "app-a":
            return FakeFeishuAdapter(crash_after=0, **kw)
        return FakeFeishuAdapter(**kw)

    manager = AppManager(
        registry=registry,
        adapter_factory=factory,
        dispatcher=dispatcher,
        on_message=lambda msg: None,
        environ=ENVIRON,
        sleep=lambda sec: sleeps.append(sec),
    )
    try:
        manager.start_all()
        # 監督迴圈在 adapter.start() 執行期間會短暫標記 connected，
        # 單次取樣有競態；改為在視窗內連續取樣：
        # app-b 必須全程 connected（故障隔離），app-a 必須進入過退避重連。
        app_b_states = set()
        app_a_states = set()
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
        assert sleeps and sleeps[0] == 1  # app-a 在退避重連
    finally:
        manager.stop_all()


def test_poll_sets_follow_snapshot_per_app(stack):
    _, _, manager, adapters, _, _ = stack
    assert adapters["app-a"].kw["poll_chat_ids"]() == ["oc_shared"]
    assert adapters["app-b"].kw["poll_chat_ids"]() == ["oc_shared"]
