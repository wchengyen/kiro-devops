import logging

from adapters.base import IncomingMessage, OutgoingPayload, PlatformAdapter
from platform_dispatcher import PlatformDispatcher


class FakeAdapter(PlatformAdapter):
    def __init__(self, name, app_key="default"):
        self._name = name
        self.app_key = app_key
        self.sent = []
        self.replies = []

    @property
    def platform(self):
        return self._name

    def start(self):
        pass

    def send_text(self, raw_user_id, text, context_token=None):
        self.sent.append((raw_user_id, text, context_token))

    def reply(self, incoming, payload):
        self.replies.append((incoming, payload))

    def upload_image(self, path):
        return "img_key"

    def upload_file(self, path):
        return "file_key"


def test_incoming_message_app_key_defaults_to_default():
    msg = IncomingMessage(
        platform="feishu",
        raw_user_id="ou_1",
        unified_user_id="feishu:ou_1",
        message_id="m1",
        text="hi",
    )
    assert msg.app_key == "default"


def test_register_two_feishu_apps_and_look_up_by_app_key():
    d = PlatformDispatcher()
    app_a = FakeAdapter("feishu", app_key="app-a")
    app_b = FakeAdapter("feishu", app_key="app-b")
    d.register(app_a)
    d.register(app_b)

    assert d.get_adapter("feishu", "app-a") is app_a
    assert d.get_adapter("feishu", "app-b") is app_b
    assert d.get_adapter("feishu", "missing") is None


def test_explicit_app_key_overrides_adapter_attribute():
    d = PlatformDispatcher()
    fake = FakeAdapter("feishu")
    d.register(fake, app_key="ops-bot")
    assert d.get_adapter("feishu", "ops-bot") is fake
    assert d.get_adapter("feishu") is None


def test_send_with_app_scoped_unified_id_routes_to_matching_app():
    d = PlatformDispatcher()
    app_a = FakeAdapter("feishu", app_key="app-a")
    app_b = FakeAdapter("feishu", app_key="app-b")
    d.register(app_a)
    d.register(app_b)

    d.send("feishu/app-b:ou_1", "hello")

    assert app_a.sent == []
    assert app_b.sent == [("ou_1", "hello", None)]


def test_legacy_unified_id_uses_default_app_key():
    d = PlatformDispatcher()
    fake = FakeAdapter("feishu")
    d.register(fake)

    d.send("feishu:ou_1", "hello")

    assert fake.sent == [("ou_1", "hello", None)]


def test_unknown_app_key_logs_error_without_sending(caplog):
    d = PlatformDispatcher()
    fake = FakeAdapter("feishu", app_key="app-a")
    d.register(fake)
    with caplog.at_level(logging.ERROR):
        d.send("feishu/app-b:ou_1", "hello")

    assert fake.sent == []
    assert "未知平台或 App: feishu/app-b" in caplog.text
