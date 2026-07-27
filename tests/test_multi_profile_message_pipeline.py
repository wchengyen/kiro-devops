import pytest

from adapters.base import IncomingMessage, OutgoingPayload
from multi_profile.health import ProfileHealth
from multi_profile.message_pipeline import MultiProfilePipeline
from multi_profile.models import AppConfig, ProfileConfig, RouteConfig, create_snapshot


class FakeDispatcher:
    def __init__(self):
        self.adapters = {}

    def register(self, adapter, app_key=None):
        self.adapters[(adapter.platform, app_key or getattr(adapter, "app_key", "default"))] = adapter

    def get_adapter(self, platform, app_key="default"):
        return self.adapters.get((platform, app_key))


class FakeAdapter:
    platform = "feishu"

    def __init__(self, app_key):
        self.app_key = app_key
        self.replies = []

    def reply(self, incoming, payload):
        self.replies.append((incoming, payload))


class FakeRuntime:
    def __init__(self):
        self.executed = []
        self.busy = set()
        self.cancelled = []

    def is_busy(self, context):
        return context.principal_key in self.busy

    def status(self, context):
        return None

    def cancel(self, context):
        self.cancelled.append(context.principal_key)
        return True

    def execute(self, context, prompt, **callbacks):
        self.executed.append((context, prompt, callbacks))


class FakeSessionStore:
    def __init__(self):
        self.cleared = []

    def clear_active(self, principal_key):
        self.cleared.append(principal_key)


class FakeAlertRunner:
    def __init__(self):
        self.ran = []

    def run(self, context, incoming, record, should_analyze=False):
        self.ran.append((context, incoming, record))


class FakeRegistry:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


ALERT_TEXT = "告警名称：CPUHigh\n告警级别：critical"


def parse_alert(text):
    if "告警名称" in text:
        return {"title": "CPUHigh", "severity": "critical"}
    return None


def make_snapshot(routes=(), app_enabled=True, profile_enabled=True):
    apps = {
        "app-a": AppConfig(
            app_key="app-a",
            enabled=app_enabled,
            app_id_env="A_ID",
            app_secret_env="A_SECRET",
            default_profile="prod-cn",
        ),
        "app-b": AppConfig(
            app_key="app-b",
            app_id_env="B_ID",
            app_secret_env="B_SECRET",
            default_profile="prod-cn",
        ),
    }
    profiles = {
        "prod-cn": ProfileConfig(
            profile_id="prod-cn",
            enabled=profile_enabled,
            aws_profile="production",
            expected_account_id="123456789012",
            working_dir="/tmp",
        )
    }
    return create_snapshot(7, apps, profiles, tuple(routes))


def make_pipeline(snapshot, health_monitor=None):
    dispatcher = FakeDispatcher()
    adapter_a, adapter_b = FakeAdapter("app-a"), FakeAdapter("app-b")
    dispatcher.register(adapter_a)
    dispatcher.register(adapter_b)
    runtime = FakeRuntime()
    store = FakeSessionStore()
    alerts = FakeAlertRunner()
    pipeline = MultiProfilePipeline(
        registry=FakeRegistry(snapshot),
        dispatcher=dispatcher,
        runtime=runtime,
        session_store=store,
        alert_runner=alerts,
        parse_alert=parse_alert,
        health_monitor=health_monitor,
    )
    return pipeline, adapter_a, adapter_b, runtime, store, alerts


def group_message(text="hello", app_key="app-a", chat_id="oc_1", is_at=True):
    return IncomingMessage(
        platform="feishu",
        app_key=app_key,
        raw_user_id="ou_1",
        unified_user_id=f"feishu:ou_1",
        message_id="m1",
        text=text,
        chat_type="group",
        is_at_me=is_at,
        group_id=chat_id,
    )


def test_mapped_group_at_message_executes_with_context():
    snapshot = make_snapshot([RouteConfig("app-a", "oc_1", "prod-cn")])
    pipeline, adapter_a, _, runtime, _, _ = make_pipeline(snapshot)

    pipeline.handle(group_message())

    assert len(runtime.executed) == 1
    context, prompt, _ = runtime.executed[0]
    assert context.profile_id == "prod-cn"
    assert context.config_generation == 7
    assert context.principal_key == "feishu/app-a/group/oc_1/user/ou_1"
    assert "正在处理" in adapter_a.replies[0][1].text


def test_unmapped_group_at_message_gets_explicit_refusal_without_runtime():
    snapshot = make_snapshot([])
    pipeline, adapter_a, _, runtime, _, _ = make_pipeline(snapshot)

    pipeline.handle(group_message(chat_id="oc_unknown"))

    assert runtime.executed == []
    assert len(adapter_a.replies) == 1
    assert "未配置" in adapter_a.replies[0][1].text


def test_unmapped_group_alert_gets_explicit_refusal_without_analysis():
    snapshot = make_snapshot([])
    pipeline, adapter_a, _, runtime, _, alerts = make_pipeline(snapshot)

    pipeline.handle(group_message(text=ALERT_TEXT, chat_id="oc_unknown", is_at=False))

    assert runtime.executed == []
    assert alerts.ran == []
    assert len(adapter_a.replies) == 1
    assert "未配置" in adapter_a.replies[0][1].text


def test_unmapped_group_plain_poll_message_stays_silent():
    snapshot = make_snapshot([])
    pipeline, adapter_a, _, runtime, _, _ = make_pipeline(snapshot)

    pipeline.handle(group_message(text="大家早", chat_id="oc_unknown", is_at=False))

    assert adapter_a.replies == []
    assert runtime.executed == []


def test_mapped_group_plain_message_without_at_stays_silent():
    snapshot = make_snapshot([RouteConfig("app-a", "oc_1", "prod-cn")])
    pipeline, adapter_a, _, runtime, _, _ = make_pipeline(snapshot)

    pipeline.handle(group_message(text="大家早", is_at=False))

    assert adapter_a.replies == []
    assert runtime.executed == []


def test_mapped_group_alert_goes_to_alert_runner_not_runtime():
    snapshot = make_snapshot([RouteConfig("app-a", "oc_1", "prod-cn")])
    pipeline, adapter_a, _, runtime, _, alerts = make_pipeline(snapshot)

    pipeline.handle(group_message(text=ALERT_TEXT, is_at=False))

    assert runtime.executed == []
    assert len(alerts.ran) == 1
    context, incoming, record = alerts.ran[0]
    assert context.group_scope_key == "feishu/app-a/group/oc_1"
    assert record["title"] == "CPUHigh"


def test_private_chat_uses_app_default_profile():
    snapshot = make_snapshot([])
    pipeline, _, adapter_b, runtime, _, _ = make_pipeline(snapshot)
    msg = IncomingMessage(
        platform="feishu",
        app_key="app-b",
        raw_user_id="ou_9",
        unified_user_id="feishu:ou_9",
        message_id="m2",
        text="hi",
        chat_type="private",
    )

    pipeline.handle(msg)

    context, _, _ = runtime.executed[0]
    assert context.profile_id == "prod-cn"
    assert context.principal_key == "feishu/app-b/private/ou_9"
    assert context.group_scope_key is None


def test_unknown_app_private_chat_gets_refusal():
    snapshot = make_snapshot([])
    pipeline, adapter_a, adapter_b, runtime, _, _ = make_pipeline(snapshot)
    msg = IncomingMessage(
        platform="feishu",
        app_key="ghost",
        raw_user_id="ou_9",
        unified_user_id="feishu:ou_9",
        message_id="m3",
        text="hi",
        chat_type="private",
    )

    pipeline.handle(msg)

    assert runtime.executed == []
    # 未知 App 無法路由回覆（沒有註冊的 adapter），只記錄日誌，不崩潰


def test_disabled_profile_group_gets_refusal():
    snapshot = make_snapshot(
        [RouteConfig("app-a", "oc_1", "prod-cn")], profile_enabled=False
    )
    # loader 不允許 route 引用 disabled profile，故直接以 snapshot 模擬熱載入邊界
    pipeline, adapter_a, _, runtime, _, _ = make_pipeline(snapshot)

    pipeline.handle(group_message())

    assert runtime.executed == []
    assert "不可用" in adapter_a.replies[0][1].text or "未配置" in adapter_a.replies[0][1].text


def test_busy_principal_gets_busy_reply():
    snapshot = make_snapshot([RouteConfig("app-a", "oc_1", "prod-cn")])
    pipeline, adapter_a, _, runtime, _, _ = make_pipeline(snapshot)
    runtime.busy.add("feishu/app-a/group/oc_1/user/ou_1")

    pipeline.handle(group_message())

    assert runtime.executed == []
    assert "还在后台运行" in adapter_a.replies[0][1].text


def test_new_command_clears_only_current_principal():
    snapshot = make_snapshot([RouteConfig("app-a", "oc_1", "prod-cn")])
    pipeline, adapter_a, _, _, store, _ = make_pipeline(snapshot)

    pipeline.handle(group_message(text="/new"))

    assert store.cleared == ["feishu/app-a/group/oc_1/user/ou_1"]
    assert "新会话" in adapter_a.replies[0][1].text


def test_cancel_command_scoped_to_principal():
    snapshot = make_snapshot([RouteConfig("app-a", "oc_1", "prod-cn")])
    pipeline, _, _, runtime, _, _ = make_pipeline(snapshot)

    pipeline.handle(group_message(text="/cancel"))

    assert runtime.cancelled == ["feishu/app-a/group/oc_1/user/ou_1"]


class FakeHealthMonitor:
    def __init__(self, health):
        self._health = health

    def health(self, profile_id):
        return self._health

    def ensure_usable(self, profile_id):
        return None


def make_health(state="active", last_sts_at=1_700_000_000.0):
    return ProfileHealth(
        profile_id="prod-cn",
        state=state,
        account_id_masked="********9012",
        last_sts_at=last_sts_at,
        last_error=None,
        consecutive_failures=0,
    )


def test_profile_command_replies_without_executing_kiro():
    snapshot = make_snapshot([RouteConfig("app-a", "oc_1", "prod-cn")])
    monitor = FakeHealthMonitor(make_health())
    pipeline, adapter_a, _, runtime, _, _ = make_pipeline(
        snapshot, health_monitor=monitor
    )

    pipeline.handle(group_message(text="/profile"))

    assert runtime.executed == []
    assert len(adapter_a.replies) == 1
    text = adapter_a.replies[0][1].text
    assert "prod-cn" in text
    assert "********9012" in text
    assert "123456789012" not in text  # 完整 Account ID 不得外洩
    assert "profile default" in text  # 未設定 region 時顯示 profile default
    assert "active" in text
    assert "2023" in text  # 最近 STS 驗證時間（last_sts_at=1_700_000_000）


def test_profile_command_unmapped_group_fails_closed():
    snapshot = make_snapshot([])
    monitor = FakeHealthMonitor(make_health())
    pipeline, adapter_a, _, runtime, _, _ = make_pipeline(
        snapshot, health_monitor=monitor
    )

    pipeline.handle(group_message(text="/profile", chat_id="oc_unknown"))

    assert runtime.executed == []
    assert len(adapter_a.replies) == 1
    assert "未配置" in adapter_a.replies[0][1].text
    assert "prod-cn" not in adapter_a.replies[0][1].text


def test_profile_command_without_health_monitor_shows_unknown():
    snapshot = make_snapshot([RouteConfig("app-a", "oc_1", "prod-cn")])
    pipeline, adapter_a, _, runtime, _, _ = make_pipeline(snapshot)

    pipeline.handle(group_message(text="/profile"))

    assert runtime.executed == []
    assert len(adapter_a.replies) == 1
    text = adapter_a.replies[0][1].text
    assert "prod-cn" in text
    assert "unknown" in text
