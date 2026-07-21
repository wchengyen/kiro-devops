import os
import time
from unittest.mock import MagicMock

import pytest

from adapters.base import IncomingMessage
from multi_profile.group_alerts import GroupAlertRunner, resolve_alert_action
from multi_profile.models import AppConfig, ProfileConfig, RouteConfig, create_snapshot
from multi_profile.router import TenantRouter
from multi_profile.scoped_state import event_owner


def make_profile(**changes):
    base = dict(
        profile_id="prod-cn",
        aws_profile="production",
        aws_region="cn-northwest-1",
        expected_account_id="123456789012",
        kiro_agent="my-dev-bot",
        model="claude-sonnet",
        alert_agent="ec2-alert-analyzer",
        alert_model=None,
        working_dir="/tmp",
        alert_timeout=300,
    )
    base.update(changes)
    return ProfileConfig(**base)


def make_context(profile, app_key="app-a", chat_id="oc_1"):
    snapshot = create_snapshot(
        3,
        {
            app_key: AppConfig(
                app_key=app_key,
                app_id_env="A_ID",
                app_secret_env="A_SECRET",
                default_profile=profile.profile_id,
            )
        },
        {profile.profile_id: profile},
        (RouteConfig(app_key, chat_id, profile.profile_id),),
    )
    return TenantRouter(snapshot).resolve(
        platform="feishu",
        app_key=app_key,
        chat_type="group",
        chat_id=chat_id,
        user_id="ou_1",
    )


def make_incoming(app_key="app-a", chat_id="oc_1"):
    return IncomingMessage(
        platform="feishu",
        app_key=app_key,
        raw_user_id="ou_1",
        unified_user_id="feishu:ou_1",
        message_id="om_1",
        text="告警名称：CPUHigh\n告警级别：critical",
        chat_type="group",
        group_id=chat_id,
    )


# ---- resolve_alert_action 優先順序 ----

def test_agent_precedence_mapping_action_wins():
    profile = make_profile(alert_agent="profile-alert-agent")
    resolved = resolve_alert_action(
        {"agent": "mapping-agent"}, profile,
        default_agent="ec2-alert-analyzer", default_tools=["execute_bash"],
        default_timeout=120, background_model="",
    )
    assert resolved.agent == "mapping-agent"


def test_agent_falls_back_to_profile_alert_agent_then_default():
    profile = make_profile(alert_agent="profile-alert-agent")
    resolved = resolve_alert_action(
        {}, profile,
        default_agent="ec2-alert-analyzer", default_tools=["execute_bash"],
        default_timeout=120, background_model="",
    )
    assert resolved.agent == "profile-alert-agent"

    resolved_default = resolve_alert_action(
        {}, make_profile(alert_agent="ec2-alert-analyzer"),
        default_agent="ec2-alert-analyzer", default_tools=["execute_bash"],
        default_timeout=120, background_model="",
    )
    assert resolved_default.agent == "ec2-alert-analyzer"


def test_model_precedence_alert_model_then_model_then_background():
    assert resolve_alert_action(
        {}, make_profile(alert_model="alert-m", model="chat-m"),
        default_agent="a", default_tools=[], default_timeout=120,
        background_model="bg-m",
    ).model == "alert-m"
    assert resolve_alert_action(
        {}, make_profile(model="chat-m"),
        default_agent="a", default_tools=[], default_timeout=120,
        background_model="bg-m",
    ).model == "chat-m"
    assert resolve_alert_action(
        {}, make_profile(model=None),
        default_agent="a", default_tools=[], default_timeout=120,
        background_model="bg-m",
    ).model == "bg-m"


def test_timeout_precedence_action_then_profile_then_default():
    assert resolve_alert_action(
        {"timeout": 999}, make_profile(alert_timeout=300),
        default_agent="a", default_tools=[], default_timeout=120,
        background_model="",
    ).timeout == 999
    assert resolve_alert_action(
        {}, make_profile(alert_timeout=456),
        default_agent="a", default_tools=[], default_timeout=120,
        background_model="",
    ).timeout == 456


def test_alert_mapping_cannot_override_aws_identity():
    profile = make_profile(aws_profile="production", aws_region="cn-northwest-1")
    resolved = resolve_alert_action(
        {"aws_profile": "attacker", "env": {"AWS_PROFILE": "attacker"},
         "aws_region": "us-east-1"},
        profile,
        default_agent="a", default_tools=[], default_timeout=120,
        background_model="",
    )
    # 解析結果不含任何 AWS 欄位；AWS 只能來自 ExecutionContext
    assert not hasattr(resolved, "aws_profile")
    assert not hasattr(resolved, "env")


# ---- GroupAlertRunner ----

class FakeAdapter:
    platform = "feishu"

    def __init__(self, app_key):
        self.app_key = app_key
        self.replies = []

    def reply(self, incoming, payload):
        self.replies.append((incoming, payload))


class FakeDispatcher:
    def __init__(self, *adapters):
        self._adapters = {(a.platform, a.app_key): a for a in adapters}

    def get_adapter(self, platform, app_key="default"):
        return self._adapters.get((platform, app_key))


def test_dedup_key_uses_group_scope_key():
    runner = GroupAlertRunner(dispatcher=FakeDispatcher())
    ctx_a = make_context(make_profile(), app_key="app-a", chat_id="oc_shared")
    ctx_b = make_context(make_profile(), app_key="app-b", chat_id="oc_shared")
    record = {"title": "CPUHigh"}

    assert runner.dedup_key(ctx_a, record) != runner.dedup_key(ctx_b, record)
    assert runner.dedup_key(ctx_a, record)[0] == "feishu/app-a/group/oc_shared"


def test_ingest_uses_scoped_event_id_and_group_owner(tmp_path):
    from event_store import EventStore

    store = EventStore(tmp_path / "events.db")
    runner = GroupAlertRunner(dispatcher=FakeDispatcher(), event_store=store)
    context = make_context(make_profile())
    incoming = make_incoming()
    record = {"title": "CPUHigh", "severity": "critical"}

    runner.run(context, incoming, record, should_analyze=False)

    events = store.list_events(event_owner(context), days=1)
    assert len(events) == 1
    assert events[0]["title"] == "CPUHigh"
    # 同一 message_id 的告警重複進來不會重複入庫
    runner.run(context, incoming, record, should_analyze=False)
    assert len(store.list_events(event_owner(context), days=1)) == 1


def test_same_external_alert_ingested_per_scope(tmp_path):
    from event_store import EventStore

    store = EventStore(tmp_path / "events.db")
    runner = GroupAlertRunner(dispatcher=FakeDispatcher(), event_store=store)
    record = {"title": "CPUHigh", "severity": "critical"}
    ctx_a = make_context(make_profile(), app_key="app-a", chat_id="oc_shared")
    ctx_b = make_context(make_profile(), app_key="app-b", chat_id="oc_shared")

    runner.run(ctx_a, make_incoming("app-a", "oc_shared"), record, should_analyze=False)
    runner.run(ctx_b, make_incoming("app-b", "oc_shared"), record, should_analyze=False)

    assert len(store.list_events(event_owner(ctx_a), days=1)) == 1
    assert len(store.list_events(event_owner(ctx_b), days=1)) == 1


def test_analysis_replies_via_originating_app():
    adapter_a, adapter_b = FakeAdapter("app-a"), FakeAdapter("app-b")
    runner = GroupAlertRunner(
        dispatcher=FakeDispatcher(adapter_a, adapter_b),
        run_analysis=lambda record, context: ("分析結果", "agent-x"),
    )
    context = make_context(make_profile(), app_key="app-b", chat_id="oc_1")

    runner.run(context, make_incoming("app-b"), {"title": "CPUHigh"}, should_analyze=True)

    assert adapter_a.replies == []
    assert len(adapter_b.replies) == 1
    assert "分析結果" in adapter_b.replies[0][1].text


def test_analysis_failure_replies_in_group_with_trace_id_no_fallback():
    adapter = FakeAdapter("app-a")
    traces = iter(["trace123abcd"])

    def failing(record, context):
        raise RuntimeError("kiro exploded")

    runner = GroupAlertRunner(
        dispatcher=FakeDispatcher(adapter),
        run_analysis=failing,
        trace_id_factory=lambda: next(traces),
    )
    context = make_context(make_profile())

    runner.run(context, make_incoming(), {"title": "CPUHigh"}, should_analyze=True)

    assert len(adapter.replies) == 1
    text = adapter.replies[0][1].text
    assert "失敗" in text
    assert "trace123abcd" in text


def test_alert_analysis_uses_context_aws_env_and_no_session(monkeypatch):
    """端到端斷言：run_alert_analysis(record, context) 的 env/argv 正確。"""
    captured = {}

    import alert_analysis
    import subprocess

    class FakeProc:
        pid = 12345

        def communicate(self, timeout=None):
            captured["timeout"] = timeout
            return ("ok", "")

        def wait(self):
            return 0

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        return FakeProc()

    monkeypatch.setattr(alert_analysis.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("AWS_PROFILE", "gateway-profile")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_LEAK")
    monkeypatch.setenv("BACKGROUND_MODEL", "bg-model")

    profile = make_profile(
        aws_profile="production", aws_region="cn-northwest-1",
        alert_model="alert-m", working_dir="/tmp",
    )
    context = make_context(profile)

    message, agent = alert_analysis.run_alert_analysis(
        {"title": "CPUHigh", "severity": "critical"}, context=context
    )

    env = captured["env"]
    assert env["AWS_PROFILE"] == "production"
    assert env["AWS_DEFAULT_PROFILE"] == "production"
    assert env["AWS_REGION"] == "cn-northwest-1"
    assert "AWS_ACCESS_KEY_ID" not in env  # 父程序靜態 credential 已清除
    assert captured["cwd"] == "/tmp"
    cmd = captured["cmd"]
    assert "--resume-id" not in cmd and "--resume" not in cmd
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "alert-m"
    assert agent == "ec2-alert-analyzer"


def test_legacy_run_alert_analysis_without_context_unchanged(monkeypatch):
    captured = {}

    import alert_analysis

    class FakeProc:
        pid = 1

        def communicate(self, timeout=None):
            return ("ok", "")

    def fake_popen(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(alert_analysis.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("BACKGROUND_MODEL", "bg-model")

    alert_analysis.run_alert_analysis({"title": "X", "severity": "high"})

    assert captured["env"]["NO_COLOR"] == "1"
    assert captured["cwd"] == os.path.expanduser("~")
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "bg-model"
