import json
import threading
import time
from unittest.mock import MagicMock

from adapters.feishu import FeishuAdapter


def make_adapter(**kwargs):
    kwargs.setdefault("app_id", "cli_test")
    kwargs.setdefault("app_secret", "secret_test")
    kwargs.setdefault("on_message", lambda msg: None)
    return FeishuAdapter(**kwargs)


def make_lark_message(message_id="om_1", chat_id="oc_1", create_time_ms=None):
    msg = MagicMock()
    msg.message_id = message_id
    msg.chat_id = chat_id
    msg.msg_type = "text"
    msg.body.content = json.dumps({"text": "hello"})
    msg.mentions = []
    msg.sender.id = "ou_sender"
    msg.create_time = str(create_time_ms or int(time.time() * 1000))
    return msg


def test_adapter_default_app_key_is_default():
    adapter = make_adapter()
    assert adapter.app_key == "default"


def test_ws_incoming_message_carries_adapter_app_key():
    received = []
    adapter = make_adapter(app_key="app-a", on_message=received.append)

    data = MagicMock()
    data.event.message.message_id = "om_ws_1"
    data.event.message.message_type = "text"
    data.event.message.chat_type = "group"
    data.event.message.chat_id = "oc_1"
    data.event.message.content = json.dumps({"text": "hi"})
    data.event.message.mentions = [MagicMock(key="@_user_1")]
    data.event.sender.sender_id.open_id = "ou_1"
    data.event.sender.name = "user"

    adapter._on_lark_message(data)

    assert len(received) == 1
    assert received[0].app_key == "app-a"


def test_dedup_cache_is_per_adapter_instance():
    received_a, received_b = [], []
    adapter_a = make_adapter(app_key="app-a", on_message=received_a.append)
    adapter_b = make_adapter(app_key="app-b", on_message=received_b.append)

    def fire(adapter):
        data = MagicMock()
        data.event.message.message_id = "om_same"
        data.event.message.message_type = "text"
        data.event.message.chat_type = "group"
        data.event.message.chat_id = "oc_shared"
        data.event.message.content = json.dumps({"text": "hi"})
        data.event.message.mentions = [MagicMock(key="@_user_1")]
        data.event.sender.sender_id.open_id = "ou_1"
        data.event.sender.name = "user"
        adapter._on_lark_message(data)

    fire(adapter_a)
    fire(adapter_b)

    assert len(received_a) == 1
    assert len(received_b) == 1  # 相同 message_id 跨 App 不可互相去重


def test_same_adapter_drops_duplicate_message_id():
    received = []
    adapter = make_adapter(on_message=received.append)

    def fire():
        data = MagicMock()
        data.event.message.message_id = "om_dup"
        data.event.message.message_type = "text"
        data.event.message.chat_type = "group"
        data.event.message.chat_id = "oc_1"
        data.event.message.content = json.dumps({"text": "hi"})
        data.event.message.mentions = [MagicMock(key="@_user_1")]
        data.event.sender.sender_id.open_id = "ou_1"
        data.event.sender.name = "user"
        adapter._on_lark_message(data)

    fire()
    fire()

    assert len(received) == 1


def test_dynamic_poll_chat_ids_callable_is_reevaluated():
    chats = {"current": ["oc_1"]}
    adapter = make_adapter(poll_chat_ids=lambda: list(chats["current"]))
    adapter._last_poll_time["oc_1"] = 100

    assert adapter._current_poll_chat_ids() == ["oc_1"]
    chats["current"] = ["oc_2"]
    assert adapter._current_poll_chat_ids() == ["oc_2"]


def test_static_poll_list_does_not_read_environment(monkeypatch):
    monkeypatch.setenv("FEISHU_POLL_CHAT_IDS", "oc_env")
    adapter = make_adapter(poll_chat_ids=["oc_explicit"])
    assert adapter._current_poll_chat_ids() == ["oc_explicit"]


def test_poll_error_in_one_chat_does_not_stop_others():
    adapter = make_adapter(poll_chat_ids=["oc_bad", "oc_good"])
    adapter._running = True
    adapter._last_poll_time = {"oc_bad": 100, "oc_good": 100}
    adapter._poll_interval = 0

    calls = []

    def fake_poll(chat_id):
        calls.append(chat_id)
        if chat_id == "oc_bad":
            raise RuntimeError("boom")
        adapter._last_poll_time[chat_id] = 200

    adapter._poll_single_chat = fake_poll

    def stop_soon():
        time.sleep(0.05)
        adapter._running = False

    threading.Thread(target=stop_soon, daemon=True).start()
    adapter._poll_loop()

    assert "oc_good" in calls
    assert "oc_bad" in adapter.poll_errors
    assert "boom" in adapter.poll_errors["oc_bad"]
    assert "oc_good" not in adapter.poll_errors


def test_ws_exit_invokes_on_disconnected_callback():
    events = []
    adapter = make_adapter(on_disconnected=lambda exc: events.append(exc))

    class FakeWsClient:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("ws died")

    import adapters.feishu as feishu_module

    original_lark = feishu_module.lark
    fake_lark = MagicMock()
    fake_lark.ws.Client = FakeWsClient
    fake_lark.EventDispatcherHandler = original_lark.EventDispatcherHandler
    fake_lark.LogLevel = original_lark.LogLevel
    feishu_module.lark = fake_lark
    try:
        adapter._ws_loop()
    finally:
        feishu_module.lark = original_lark

    assert len(events) == 1
    assert isinstance(events[0], RuntimeError)


def test_stop_releases_start_block():
    adapter = make_adapter(poll_chat_ids=[])
    adapter._ws_loop = lambda: adapter._shutdown_event.wait(0.05) or None

    thread = threading.Thread(target=adapter.start, daemon=True)
    thread.start()
    time.sleep(0.05)
    adapter.stop()
    thread.join(timeout=2)

    assert not thread.is_alive()
