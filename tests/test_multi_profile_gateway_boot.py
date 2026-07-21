import threading
from unittest.mock import MagicMock

import pytest

import gateway


@pytest.fixture(autouse=True)
def no_threads(monkeypatch):
    """所有模式都不真的啟動執行緒或 webhook。"""
    monkeypatch.setattr(gateway.threading, "Thread", lambda **kw: MagicMock(start=lambda: None))
    monkeypatch.setattr(gateway, "start_webhook_server", lambda *a, **kw: None)
    monkeypatch.setattr(gateway, "_keep_alive", lambda threads: None)


def write_config(tmp_path):
    path = tmp_path / "multi_profile_config.yaml"
    path.write_text(
        f"""
version: 1
apps:
  app-a:
    app_id_env: APP_A_APP_ID
    app_secret_env: APP_A_APP_SECRET
    default_profile: prod-cn
  app-b:
    app_id_env: APP_B_APP_ID
    app_secret_env: APP_B_APP_SECRET
    default_profile: prod-cn
profiles:
  prod-cn:
    aws_profile: production
    expected_account_id: "123456789012"
    working_dir: {tmp_path}
routes:
  - app: app-a
    chat_id: oc_1
    profile: prod-cn
    poll_alerts: true
""",
        encoding="utf-8",
    )
    return path


def test_legacy_mode_builds_single_default_app_feishu(monkeypatch):
    monkeypatch.setattr(gateway, "APP_ID", "cli_legacy")
    monkeypatch.setattr(gateway, "APP_SECRET", "secret_legacy")
    monkeypatch.setattr(gateway, "WEIXIN_BOT_TOKEN", None)
    monkeypatch.setattr(gateway, "is_enabled", lambda: False)
    monkeypatch.setattr(gateway.os, "environ", {"WEBHOOK_ENABLED": "false"})

    built = []

    class FakeFeishu:
        platform = "feishu"

        def __init__(self, **kw):
            built.append(kw)

        def start(self):
            pass

    class FakeWeixin:
        platform = "weixin"
        app_key = "default"

        def __init__(self, **kw):
            pass

        def start(self):
            pass

    monkeypatch.setattr(gateway, "FeishuAdapter", FakeFeishu)
    monkeypatch.setattr(gateway, "WeixinAdapter", FakeWeixin)
    app_manager_cls = MagicMock()
    monkeypatch.setattr(gateway, "AppManager", app_manager_cls)

    dispatcher = gateway.build_gateway()

    assert len(built) == 1
    assert built[0]["app_id"] == "cli_legacy"
    assert dispatcher.get_adapter("feishu") is not None
    assert dispatcher.get_adapter("feishu", "app-a") is None
    app_manager_cls.assert_not_called()  # legacy 不得建立 AppManager


def test_multi_profile_mode_registers_all_apps_no_legacy_adapter(monkeypatch, tmp_path):
    config = write_config(tmp_path)
    env = {
        "MULTI_PROFILE_ENABLED": "true",
        "MULTI_PROFILE_CONFIG": str(config),
        "APP_A_APP_ID": "cli_a", "APP_A_APP_SECRET": "sa",
        "APP_B_APP_ID": "cli_b", "APP_B_APP_SECRET": "sb",
        "WEBHOOK_ENABLED": "false",
    }
    monkeypatch.setattr(gateway.os, "environ", env)
    monkeypatch.setattr(gateway, "APP_ID", "cli_legacy")
    monkeypatch.setattr(gateway, "APP_SECRET", "secret_legacy")
    monkeypatch.setattr(gateway, "WEIXIN_BOT_TOKEN", None)
    monkeypatch.setattr(gateway, "is_enabled", lambda: True)
    monkeypatch.setattr(gateway, "config_path", lambda **kw: config)

    built = []

    class FakeFeishu:
        platform = "feishu"

        def __init__(self, **kw):
            built.append(kw)
            self.app_key = kw.get("app_key", "default")
            self.poll_errors = {}

        def start(self):
            pass

        def stop(self):
            pass

    class FakeWeixin:
        platform = "weixin"
        app_key = "default"

        def __init__(self, **kw):
            pass

        def start(self):
            pass

    monkeypatch.setattr(gateway, "FeishuAdapter", FakeFeishu)
    monkeypatch.setattr(gateway, "WeixinAdapter", FakeWeixin)

    dispatcher = gateway.build_gateway()

    # 所有飛書 adapter 都帶 app_key（經 AppManager 建立）；
    # legacy 建構（無 app_key）不存在 → 無重複 WS 連線
    assert len(built) == 2
    assert all("app_key" in kw for kw in built)
    assert {kw["app_key"] for kw in built} == {"app-a", "app-b"}
    assert dispatcher.get_adapter("feishu", "app-a") is not None
    assert dispatcher.get_adapter("feishu", "app-b") is not None
    assert dispatcher.get_adapter("feishu") is None  # 沒有 default 飛書 adapter


def test_multi_profile_invalid_config_disables_feishu_but_keeps_gateway(monkeypatch, tmp_path):
    env = {
        "MULTI_PROFILE_ENABLED": "true",
        "MULTI_PROFILE_CONFIG": str(tmp_path / "missing.yaml"),
        "WEBHOOK_ENABLED": "false",
    }
    monkeypatch.setattr(gateway.os, "environ", env)
    monkeypatch.setattr(gateway, "APP_ID", "cli_legacy")
    monkeypatch.setattr(gateway, "APP_SECRET", "secret_legacy")
    monkeypatch.setattr(gateway, "WEIXIN_BOT_TOKEN", "wx-token")
    monkeypatch.setattr(gateway, "is_enabled", lambda: True)
    monkeypatch.setattr(
        gateway, "config_path", lambda **kw: tmp_path / "missing.yaml"
    )

    class FakeWeixin:
        platform = "weixin"
        app_key = "default"

        def __init__(self, **kw):
            pass

        def start(self):
            pass

    monkeypatch.setattr(gateway, "WeixinAdapter", FakeWeixin)

    dispatcher = gateway.build_gateway()  # 不得拋出、不得退回 legacy

    assert dispatcher.get_adapter("feishu") is None
    assert dispatcher.get_adapter("weixin") is not None
