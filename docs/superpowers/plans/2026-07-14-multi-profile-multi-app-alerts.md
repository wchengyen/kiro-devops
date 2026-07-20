# 多 App 與群告警整合實作計畫

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推薦）或 superpowers:executing-plans 逐任務實現此計畫。步驟使用復選框（`- [ ]`）語法追蹤進度。

**目標：** 把計畫 1–2 建立的核心接到真實訊息路徑：`IncomingMessage` 帶 `app_key`、`PlatformDispatcher` 以 `(platform, app_key)` 註冊查找、`AppManager` 建立並監督多個 `FeishuAdapter`（獨立 WS／輪詢生命週期、指數退避重連、狀態追蹤）、輪詢群集合改由 snapshot 路由動態決定、`MessageHandler` 經 `TenantRouter` fail-closed 路由，群告警分析全程使用 `ExecutionContext` 並由原 App 回覆原群。

**架構：** 新監督與整合邏輯放在 `multi_profile` package（`app_manager.py`、`poll_sets.py`、`message_pipeline.py`、`group_alerts.py`）；對既有 `adapters/base.py`、`adapters/feishu.py`、`platform_dispatcher.py`、`alert_analysis.py`、`message_handler.py`、`gateway.py` 只做向後相容的最小修改（所有新參數都有保留現行行為的預設值）。`MULTI_PROFILE_ENABLED=false` 時生產行為完全不變，且不會建立重複 WebSocket 連線。

**技術棧：** Python 3.10+、標準庫 `threading`／`uuid`／`enum`／`dataclasses`、lark-oapi、pytest、計畫 1–2 的 `multi_profile` 模組。

**依賴：** 必須先完整實作並驗證：
- `docs/superpowers/plans/2026-07-14-multi-profile-routing-core.md`（計畫 1）
- `docs/superpowers/plans/2026-07-14-multi-profile-runtime-session-isolation.md`（計畫 2）

**參考規格：** `docs/superpowers/specs/2026-07-14-multi-profile-multi-feishu-group-design.md` 第 5、7、12、15、17、18、21.2、22 節。

---

## 檔案結構

### 建立

- `multi_profile/poll_sets.py`：由 snapshot 計算某 App 的動態輪詢群集合（`poll_alerts: true` 的路由）。
- `multi_profile/app_manager.py`：依 snapshot 建立、註冊並監督多個 FeishuAdapter；指數退避重連；per-app 狀態（`connected/disconnected/reconnecting/pending-restart`）。
- `multi_profile/message_pipeline.py`：多 profile 訊息管線；TenantRouter 路由、fail-closed 拒絕、命令分派、ContextRuntime 執行、原 App 回覆。
- `multi_profile/group_alerts.py`：群告警的 Agent／模型優先順序解析、scope 去重與事件入庫、原 App 回覆與 trace ID 錯誤回覆。
- `tests/test_multi_profile_dispatcher.py`：`(platform, app_key)` registry 與向後相容。
- `tests/test_multi_profile_feishu_adapter.py`：多實例 app_key、per-app 去重、動態輪詢集合、單群錯誤隔離、斷線回呼。
- `tests/test_multi_profile_poll_sets.py`：動態輪詢集合計算。
- `tests/test_multi_profile_app_manager.py`：建立、監督、退避、重設、故障隔離、pending-restart。
- `tests/test_multi_profile_message_pipeline.py`：群／私聊路由、fail-closed、命令與忙碌隔離。
- `tests/test_multi_profile_group_alerts.py`：優先順序、AWS 唯來自 context、scope 隔離、原 App 回覆、錯誤 trace ID。
- `tests/test_multi_profile_gateway_boot.py`：雙模式啟動、無重複 WS、啟動失敗 fail-closed。
- `tests/test_multi_profile_integration_apps.py`：規格 §21.2 的多 App 整合與故障隔離。

### 修改

- `adapters/base.py`：`IncomingMessage` 新增 `app_key: str = "default"`。
- `platform_dispatcher.py`：registry 鍵改為 `(platform, app_key)`；`register`／`get_adapter` 向後相容。
- `adapters/feishu.py`：建構子新增 `app_key`、注入式 `poll_chat_ids`（list 或 callable）、`group_alert_listen`、`on_disconnected`、`stop()`；訊息去重改為 per-instance；產生的 `IncomingMessage` 帶 `app_key`。
- `alert_analysis.py`：`run_alert_analysis(record, context=None)`；有 context 時依規格 §12 解析 Agent／模型／逾時並使用 `build_child_env(context)`。
- `message_handler.py`：建構子新增 `mp_pipeline=None`；feishu 訊息在管線存在時委派，其餘路徑不變。
- `gateway.py`：依 `is_enabled()` 分流 legacy／multi-profile 啟動；抽出可測試的啟動函式。
- `multi_profile/__init__.py`：匯出計畫 4 可依賴的穩定介面。
- `.env.example`：補充多 App 模式的 App 憑證 env 命名範例（不含 Secret 值）。

### 明確不修改

- `kiro_executor.py`
- `session_router.py`
- `memory.py`
- `semantic_store.py`
- `event_store.py`
- `scheduler.py`
- `webhook_server.py`
- `adapters/weixin.py`、`adapters/weixin_media.py`（微信固定 `app_key = "default"`，不需修改）
- `dashboard/`（Dashboard API／UI 屬計畫 4）
- `multi_profile/models.py`、`config_loader.py`、`registry.py`、`router.py`、`runtime_env.py`、`output.py`、`process_utils.py`、`task_registry.py`、`session_store.py`、`session_capture.py`、`runtime.py`、`scoped_state.py`（計畫 1–2 已完成，本計畫只消費其介面）

---

## 執行前基線

- [ ] **記錄計畫 3 起始 SHA**

```bash
git rev-parse HEAD > .git/plan3-base-sha
cat .git/plan3-base-sha
```

預期：輸出計畫 2 完成後的 HEAD SHA。後續所有範圍驗證都讀取此檔，不使用 `HEAD~N`。

- [ ] **確認計畫 1–2 介面可匯入**

```bash
python3 - <<'PY'
from multi_profile import (
    ConfigRegistry,
    ContextRuntime,
    ExecutionContext,
    RouteNotFound,
    SessionStore,
    TenantRouter,
    build_child_env,
    config_path,
    event_owner,
    is_enabled,
    scoped_event_id,
    semantic_owner,
)
print("plan 1-2 public API import OK")
PY
```

預期：輸出 `plan 1-2 public API import OK`。失敗代表計畫 1–2 未完成，停止。

- [ ] **確認基線測試全綠**

```bash
pytest -q tests/test_multi_profile_*.py tests/test_platform_dispatcher.py tests/test_group_alert_detection.py
```

預期：0 failed。此基線用於判斷本計畫是否造成回歸。

---

### 任務 1：IncomingMessage app_key 與 (platform, app_key) Dispatcher

**文件：**
- 修改：`adapters/base.py`
- 修改：`platform_dispatcher.py`
- 建立：`tests/test_multi_profile_dispatcher.py`

- [ ] **步驟 1：編寫失敗的多 App Dispatcher 測試**

建立 `tests/test_multi_profile_dispatcher.py`：

```python
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
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_dispatcher.py
```

預期：FAIL；`IncomingMessage` 沒有 `app_key`，`register`／`get_adapter` 不接受 app_key。

- [ ] **步驟 3：在 IncomingMessage 尾端新增 app_key**

修改 `adapters/base.py` 的 `IncomingMessage`，在**最後一個欄位之後**新增（保持既有位置與關鍵字建構相容）：

```python
@dataclass
class IncomingMessage:
    platform: str
    raw_user_id: str
    unified_user_id: str
    message_id: str
    text: str
    chat_type: str = "private"
    is_at_me: bool = False
    context_token: str | None = None
    raw: dict = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    group_id: str | None = None
    group_name: str | None = None
    sender_name: str | None = None
    app_key: str = "default"
```

微信不修改：`WeixinAdapter` 建立的訊息自然取得 `app_key = "default"`（規格 §5.2）。

- [ ] **步驟 4：把 Dispatcher registry 鍵改為 (platform, app_key)**

將 `platform_dispatcher.py` 完整替換為：

```python
#!/usr/bin/env python3
"""统一发送路由，按 (platform, app_key) 注册并以统一 ID 分发到对应适配器."""
import logging

from adapters.base import PlatformAdapter

log = logging.getLogger("platform-dispatcher")

DEFAULT_APP_KEY = "default"


class PlatformDispatcher:
    def __init__(self):
        self._adapters: dict[tuple[str, str], PlatformAdapter] = {}

    def register(self, adapter: PlatformAdapter, app_key: str | None = None) -> None:
        """注册适配器。未指定 app_key 时使用适配器自带 app_key，缺省为 "default"."""
        key = (adapter.platform, app_key or getattr(adapter, "app_key", DEFAULT_APP_KEY))
        if key in self._adapters:
            log.warning(f"适配器重复注册，将覆盖: {key[0]}/{key[1]}")
        self._adapters[key] = adapter

    @staticmethod
    def _parse_unified_id(unified_user_id: str) -> tuple[str, str, str] | None:
        """解析 'platform:raw_id' 或 'platform/app_key:raw_id' 为 (platform, app_key, raw_id)."""
        if ":" not in unified_user_id:
            return None
        prefix, raw_id = unified_user_id.split(":", 1)
        if "/" in prefix:
            platform, app_key = prefix.split("/", 1)
            if not platform or not app_key:
                return None
        else:
            platform, app_key = prefix, DEFAULT_APP_KEY
        if not platform or not raw_id:
            return None
        return platform, app_key, raw_id

    def _resolve(self, unified_user_id: str):
        """解析 unified_user_id 为 (adapter, raw_id, context_token)."""
        parsed = self._parse_unified_id(unified_user_id)
        if parsed is None:
            log.error(f"非法的统一用户ID格式: {unified_user_id}")
            return None, None, None
        platform, app_key, raw_id = parsed
        adapter = self._adapters.get((platform, app_key))
        if not adapter:
            if app_key == DEFAULT_APP_KEY:
                log.error(f"未知平台: {platform}")
            else:
                log.error(f"未知平台或 App: {platform}/{app_key}")
            return None, None, None
        ctx = None
        if platform == "weixin":
            ctx = getattr(adapter, "_context_tokens", {}).get(raw_id)
        return adapter, raw_id, ctx

    def send(self, unified_user_id: str, text: str) -> None:
        adapter, raw_id, ctx = self._resolve(unified_user_id)
        if adapter:
            adapter.send_text(raw_id, text, context_token=ctx)

    def send_image(self, unified_user_id: str, image_path: str) -> bool:
        adapter, raw_id, ctx = self._resolve(unified_user_id)
        if not adapter:
            return False
        return adapter.send_image(raw_id, image_path, context_token=ctx)

    def send_file(self, unified_user_id: str, file_path: str) -> bool:
        adapter, raw_id, ctx = self._resolve(unified_user_id)
        if not adapter:
            return False
        return adapter.send_file(raw_id, file_path, context_token=ctx)

    def get_adapter(self, platform: str, app_key: str = DEFAULT_APP_KEY) -> PlatformAdapter | None:
        return self._adapters.get((platform, app_key))
```

注意：`send` 失敗只記錄日誌，**不得**改由其他 App 代發（規格 §15.2）。

- [ ] **步驟 5：執行新舊 Dispatcher 測試**

```bash
pytest -q tests/test_multi_profile_dispatcher.py tests/test_platform_dispatcher.py
```

預期：全部 PASS。舊測試（含 `未知平台: telegram` 錯誤訊息斷言）不變即通過，證明向後相容。

- [ ] **步驟 6：提交任務 1**

```bash
git add adapters/base.py platform_dispatcher.py tests/test_multi_profile_dispatcher.py
git commit -m "feat(多租戶): Dispatcher 以 (platform, app_key) 註冊查找"
```

---

### 任務 2：FeishuAdapter 多實例化、per-app 去重與斷線回呼

**文件：**
- 修改：`adapters/feishu.py`
- 建立：`tests/test_multi_profile_feishu_adapter.py`

設計要點（對應規格 §5.2、§15.2、§18）：

- 建構子新增純關鍵字參數，全部有 legacy 預設值；`gateway.py` 目前的建構方式一行都不用改。
- 訊息去重從模組級全域 `set` 改為 **per-instance**：兩個 App 同在一個群時，相同 `message_id` 必須各自投遞一次，否則第二個 App 的訊息會被靜默丟棄。
- `poll_chat_ids` 可注入靜態 list 或 **callable**（每次輪詢循環重新求值，供任務 3 的動態集合使用）；預設 `None` 維持讀取 `FEISHU_POLL_CHAT_IDS`。
- WS 迴圈結束（正常或異常）時呼叫 `on_disconnected(exc)` 回呼，供 AppManager 監督重連；legacy 不傳回呼時行為不變。
- `group_alert_listen` 顯式覆寫 `GROUP_ALERT_LISTEN_ENABLED` 環境判斷；多 profile 模式由 AppManager 固定傳 `True`，讓所有群訊息進入管線，由管線決定靜默／拒絕／告警。
- `stop()` 供 AppManager 關閉：停止輪詢並解除 `start()` 阻塞。

- [ ] **步驟 1：編寫失敗的 Adapter 測試**

建立 `tests/test_multi_profile_feishu_adapter.py`：

```python
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
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_feishu_adapter.py
```

預期：FAIL；`FeishuAdapter.__init__` 不接受 `app_key` 等參數。

- [ ] **步驟 3：修改 FeishuAdapter 建構子與去重**

在 `adapters/feishu.py` 中：

1. 刪除模組級 `_processed_message_ids` 與 `_MAX_MSG_ID_CACHE` 中的 set（保留 `_MAX_MSG_ID_CACHE = 1000` 常數）。
2. 建構子改為：

```python
def __init__(
    self,
    app_id: str,
    app_secret: str,
    on_message: Callable[[IncomingMessage], None],
    *,
    app_key: str = "default",
    poll_chat_ids: list[str] | Callable[[], list[str]] | None = None,
    poll_interval: int | None = None,
    group_alert_listen: bool | None = None,
    on_disconnected: Callable[[BaseException | None], None] | None = None,
):
    self.app_id = app_id
    self.app_secret = app_secret
    self.on_message = on_message
    self.app_key = app_key
    self._on_disconnected = on_disconnected
    self.client = lark.Client.builder() \
        .app_id(app_id) \
        .app_secret(app_secret) \
        .log_level(lark.LogLevel.INFO) \
        .build()

    # === 轮询配置 ===
    # None：legacy 模式读取 FEISHU_POLL_CHAT_IDS；
    # list：静态集合；callable：每次轮询循环重新求值（多 profile 动态集合）
    if poll_chat_ids is None:
        poll_chat_ids = [
            c.strip()
            for c in os.environ.get("FEISHU_POLL_CHAT_IDS", "").split(",")
            if c.strip()
        ]
    self._poll_chat_ids_source = poll_chat_ids
    self._poll_interval = (
        poll_interval
        if poll_interval is not None
        else int(os.environ.get("FEISHU_POLL_INTERVAL_SEC", "10"))
    )
    if group_alert_listen is None:
        group_alert_listen = os.environ.get(
            "GROUP_ALERT_LISTEN_ENABLED", "false"
        ).lower() in ("true", "1", "yes")
    self._group_alert_listen = group_alert_listen
    # 每个群的最后处理 create_time（秒），避免重复
    self._last_poll_time: dict[str, int] = {}
    # 单群轮询错误隔离：chat_id -> 最近错误摘要（成功后清除）
    self.poll_errors: dict[str, str] = {}
    # 消息去重缓存（per-instance；多 App 同群时 message_id 相同也必须各自投递）
    self._processed_message_ids: set[str] = set()
    self._running = False
    self._shutdown_event = threading.Event()
    self._bot_open_id: str | None = None
```

3. 新增實例方法：

```python
def _current_poll_chat_ids(self) -> list[str]:
    source = self._poll_chat_ids_source
    if callable(source):
        return list(source())
    return list(source)

def _remember_message_id(self, message_id: str) -> bool:
    """记录 message_id；已处理过返回 False。WS 与轮询共用以避免重推。"""
    if message_id in self._processed_message_ids:
        return False
    self._processed_message_ids.add(message_id)
    if len(self._processed_message_ids) > _MAX_MSG_ID_CACHE:
        half = list(self._processed_message_ids)[_MAX_MSG_ID_CACHE // 2:]
        self._processed_message_ids.clear()
        self._processed_message_ids.update(half)
    return True

def stop(self) -> None:
    """停止轮询并解除 start() 阻塞（供 AppManager 关闭）。"""
    self._running = False
    self._shutdown_event.set()
```

- [ ] **步驟 4：更新 start、WS 迴圈、輪詢迴圈與訊息建構**

`_ws_loop` 改為（保留 legacy 的 shutdown 行為，追加回呼）：

```python
def _ws_loop(self) -> None:
    handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(self._on_lark_message) \
        .build()
    cli = lark.ws.Client(self.app_id, self.app_secret, event_handler=handler, log_level=lark.LogLevel.INFO)
    exc: BaseException | None = None
    try:
        cli.start()
    except Exception as e:
        exc = e
        log.exception(f"飞书 WS 异常退出 (app={self.app_key})")
    finally:
        self._shutdown_event.set()
        if self._on_disconnected is not None:
            try:
                self._on_disconnected(exc)
            except Exception:
                log.exception(f"on_disconnected 回呼失敗 (app={self.app_key})")
```

`_poll_loop` 改為每輪重新求值動態集合、維護水位線與單群錯誤隔離：

```python
def _poll_loop(self) -> None:
    """定期拉取指定群的历史消息；群集合可動態變更，單群錯誤不影響其他群."""
    boot_time_sec = int(time.time()) - 30

    while self._running:
        chat_ids = self._current_poll_chat_ids()
        for cid in chat_ids:
            self._last_poll_time.setdefault(cid, boot_time_sec)
        # 移除已不在集合中的群水位线
        for stale in set(self._last_poll_time) - set(chat_ids):
            del self._last_poll_time[stale]
            self.poll_errors.pop(stale, None)

        for chat_id in chat_ids:
            if not self._running:
                break
            try:
                self._poll_single_chat(chat_id)
                self.poll_errors.pop(chat_id, None)
            except Exception as e:
                log.exception(f"轮询群消息失败: {chat_id} (app={self.app_key})")
                self.poll_errors[chat_id] = f"{type(e).__name__}: {e}"
        for _ in range(self._poll_interval):
            if not self._running:
                break
            time.sleep(1)
```

`_poll_single_chat` 中的去重段落改用 `self._remember_message_id(msg.message_id)`；`start()` 中 `self._poll_chat_ids` 引用改為 `self._current_poll_chat_ids()`（判斷是否啟動輪詢執行緒與記錄群數）。

`_on_lark_message` 與 `_dispatch_polled_message` 的 `IncomingMessage(...)` 都加入 `app_key=self.app_key`；`_on_lark_message` 的群未 @ 過濾改用 `self._group_alert_listen`（不再直接讀 env）。

- [ ] **步驟 5：執行 Adapter 測試與 legacy 回歸**

```bash
pytest -q \
  tests/test_multi_profile_feishu_adapter.py \
  tests/test_group_alert_detection.py \
  tests/test_platform_dispatcher.py
```

預期：全部 PASS。

- [ ] **步驟 6：確認 gateway 既有建構方式仍可運作**

```bash
python3 - <<'PY'
import os
os.environ.pop("FEISHU_POLL_CHAT_IDS", None)
from adapters.feishu import FeishuAdapter
adapter = FeishuAdapter(app_id="cli_x", app_secret="s", on_message=lambda m: None)
assert adapter.app_key == "default"
assert adapter._current_poll_chat_ids() == []
print("legacy FeishuAdapter construction OK")
PY
```

預期：輸出 `legacy FeishuAdapter construction OK`。

- [ ] **步驟 7：提交任務 2**

```bash
git add adapters/feishu.py tests/test_multi_profile_feishu_adapter.py
git commit -m "feat(多租戶): FeishuAdapter 支援多實例與斷線回呼"
```

---

### 任務 3：動態輪詢群集合

**文件：**
- 建立：`multi_profile/poll_sets.py`
- 建立：`tests/test_multi_profile_poll_sets.py`
- 修改：`multi_profile/__init__.py`

設計要點（對應規格 §6.3、§13.5）：多 profile 模式下，`poll_alerts: true` 的路由決定輪詢哪些群，取代靜態 `FEISHU_POLL_CHAT_IDS`。集合由每次輪詢循環時的**目前 snapshot** 計算，因此熱載入路由後無需重啟即生效（`poll_alerts` 屬可熱載入欄位）。

- [ ] **步驟 1：編寫失敗測試**

建立 `tests/test_multi_profile_poll_sets.py`：

```python
from multi_profile.models import AppConfig, ProfileConfig, RouteConfig, create_snapshot
from multi_profile.poll_sets import poll_chat_ids_for_app


def make_snapshot(routes):
    apps = {
        key: AppConfig(
            app_key=key,
            app_id_env="APP_ID",
            app_secret_env="APP_SECRET",
            default_profile="prod-cn",
        )
        for key in ("app-a", "app-b")
    }
    profiles = {
        "prod-cn": ProfileConfig(
            profile_id="prod-cn",
            aws_profile="production",
            expected_account_id="123456789012",
            working_dir="/tmp",
        )
    }
    return create_snapshot(1, apps, profiles, tuple(routes))


def test_only_poll_alerts_routes_are_included():
    snapshot = make_snapshot([
        RouteConfig("app-a", "oc_1", "prod-cn", poll_alerts=True),
        RouteConfig("app-a", "oc_2", "prod-cn", poll_alerts=False),
    ])
    assert poll_chat_ids_for_app(snapshot, "app-a") == ["oc_1"]


def test_poll_set_is_scoped_per_app():
    snapshot = make_snapshot([
        RouteConfig("app-a", "oc_shared", "prod-cn", poll_alerts=True),
        RouteConfig("app-b", "oc_shared", "prod-cn", poll_alerts=False),
        RouteConfig("app-b", "oc_b", "prod-cn", poll_alerts=True),
    ])
    assert poll_chat_ids_for_app(snapshot, "app-a") == ["oc_shared"]
    assert poll_chat_ids_for_app(snapshot, "app-b") == ["oc_b"]


def test_unknown_app_returns_empty_list():
    snapshot = make_snapshot([])
    assert poll_chat_ids_for_app(snapshot, "missing") == []


def test_result_is_sorted_and_deduplicated():
    snapshot = make_snapshot([
        RouteConfig("app-a", "oc_z", "prod-cn", poll_alerts=True),
        RouteConfig("app-a", "oc_a", "prod-cn", poll_alerts=True),
    ])
    assert poll_chat_ids_for_app(snapshot, "app-a") == ["oc_a", "oc_z"]
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_poll_sets.py
```

預期：FAIL，包含 `ModuleNotFoundError: No module named 'multi_profile.poll_sets'`。

- [ ] **步驟 3：實作 poll_sets**

建立 `multi_profile/poll_sets.py`：

```python
from __future__ import annotations

from .models import ConfigSnapshot


def poll_chat_ids_for_app(snapshot: ConfigSnapshot, app_key: str) -> list[str]:
    """回傳該 App 目前 snapshot 中 poll_alerts=true 的群集合（排序、去重）。

    取代 legacy 的 FEISHU_POLL_CHAT_IDS；路由熱載入後下一次輪詢循環即生效。
    """
    return sorted(
        {
            route.chat_id
            for route in snapshot.routes
            if route.app_key == app_key and route.poll_alerts
        }
    )
```

在 `multi_profile/__init__.py` 追加：

```python
from .poll_sets import poll_chat_ids_for_app

__all__ += ["poll_chat_ids_for_app"]
```

- [ ] **步驟 4：執行測試**

```bash
pytest -q tests/test_multi_profile_poll_sets.py
```

預期：4 passed。

- [ ] **步驟 5：提交任務 3**

```bash
git add multi_profile/poll_sets.py multi_profile/__init__.py tests/test_multi_profile_poll_sets.py
git commit -m "feat(多租戶): 由 snapshot 計算動態輪詢群集合"
```

---

### 任務 4：AppManager 建立、監督與指數退避重連

**文件：**
- 建立：`multi_profile/app_manager.py`
- 建立：`tests/test_multi_profile_app_manager.py`
- 修改：`multi_profile/__init__.py`

設計要點（對應規格 §5.1、§14.1、§15.2、§17）：

- 從 `ConfigRegistry.snapshot()` 的 `apps` 建立 Adapter；`enabled: false` 的 App 不建立連線。
- Secret **值**只從 environ（依 `app_id_env`／`app_secret_env` 名稱）讀取，永不寫入日誌；測試需斷言日誌不含 secret 值。
- 每個 App 一條監督執行緒：執行 `adapter.start()`；返回或例外 → 退避後用 factory **重新建立** Adapter（lark WS client 不可重啟）再啟動。單一 App 的崩潰迴圈不影響其他 App。
- 退避序列 `1, 2, 4, 8, 16, 32, 60`，之後維持 60 秒；連線穩定 `stable_after` 秒（預設 30）後視為成功，attempts 重設為 0。
- 狀態機：`connected`（adapter 運行中）、`disconnected`（已停止／尚未啟動）、`reconnecting`（退避等待中）、`pending-restart`（snapshot 中連線欄位變更或新增 App，需重啟才生效，見 `on_snapshot_changed`；規格 §13.5）。
- `stop_all()` 設定停止旗標並呼叫各 adapter 的 `stop()`，供 gateway 關閉。

- [ ] **步驟 1：編寫失敗的 AppManager 測試**

建立 `tests/test_multi_profile_app_manager.py`：

```python
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
                 poll_chat_ids=None, on_disconnected=None, crash_after=None):
        self.app_key = app_key
        self.app_id = app_id
        self.app_secret = app_secret
        self.on_message = on_message
        self.poll_chat_ids = poll_chat_ids
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
    manager.stop_all()

    assert sleeps[:3] == [1, 2, 4]
    assert manager.status()["app-a"]["state"] in (
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
    time.sleep(0.2)

    app_b = dispatcher.get_adapter("feishu", "app-b")
    assert app_b is not None and app_b.started.is_set()
    assert manager.status()["app-b"]["state"] == AppConnState.CONNECTED.value
    assert manager.status()["app-a"]["state"] in (
        AppConnState.RECONNECTING.value, AppConnState.DISCONNECTED.value
    )
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
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_app_manager.py
```

預期：FAIL，包含 `ModuleNotFoundError: No module named 'multi_profile.app_manager'`。

- [ ] **步驟 3：實作 AppManager**

建立 `multi_profile/app_manager.py`：

```python
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
                log.exception(f"App {app_key} 連線結束（異常）")
            if self._stop_event.is_set():
                break

            uptime = self._clock() - started_at
            supervised.attempts = self._next_attempt(
                previous_attempt=supervised.attempts, uptime_seconds=uptime
            )
            delay = self._backoff_for_attempt(supervised.attempts)
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
```

說明：`_supervise` 內重建失敗不終止監督迴圈，只增加 attempts 繼續退避；`dispatcher.register` 以相同鍵覆蓋舊 adapter，保證回覆永遠走最新實例。

在 `multi_profile/__init__.py` 追加：

```python
from .app_manager import AppConnState, AppManager

__all__ += ["AppConnState", "AppManager"]
```

- [ ] **步驟 4：執行 AppManager 測試**

```bash
pytest -q tests/test_multi_profile_app_manager.py
```

預期：全部 PASS。注意 `test_crash_triggers_reconnect_with_exponential_backoff` 使用注入 sleep，退避等待為同步記錄，整個測試應在 5 秒內完成。

- [ ] **步驟 5：提交任務 4**

```bash
git add multi_profile/app_manager.py multi_profile/__init__.py tests/test_multi_profile_app_manager.py
git commit -m "feat(多租戶): AppManager 監督多 App 與指數退避重連"
```

---

### 任務 5：MessageHandler 多 profile 路由與 fail-closed

**文件：**
- 建立：`multi_profile/message_pipeline.py`
- 建立：`tests/test_multi_profile_message_pipeline.py`
- 修改：`message_handler.py`
- 修改：`multi_profile/__init__.py`

設計要點（對應規格 §7、§12.1、§15.1）：

- 每則訊息只取一次 snapshot，`TenantRouter` 與後續回覆共用同一 generation（規格 §7.1）。
- 未映射群 fail-closed：**@Bot 普通訊息**或**可辨識告警**在原群明確拒絕；**未 @ 且非告警**的輪詢訊息靜默；所有情況都不啟動 Kiro／AWS 子程序、不使用任何 fallback profile。
- 私聊用 App `default_profile`；路由失敗（未知 App／profile 不可用）明確拒絕。
- 回覆一律經 `dispatcher.get_adapter(incoming.platform, incoming.app_key)`，即**原 App**。
- 命令（`/new`、`/resume`、`/sessions`、`/status`、`/cancel`）改用計畫 2 的 `SessionStore`／`ContextRuntime`，以 `principal_key` 為邊界；舊 `SessionRouter` 在管線中完全不出現。
- 普通對話經 `ContextRuntime.execute(context, ...)`；記憶使用 `semantic_owner(context)`。
- 群告警偵測沿用 `MessageHandler._parse_structured_alert`（以 callable 注入，避免循環匯入）；命中告警委派給任務 6 的 `GroupAlertRunner`。

- [ ] **步驟 1：編寫失敗的管線測試**

建立 `tests/test_multi_profile_message_pipeline.py`：

```python
import pytest

from adapters.base import IncomingMessage, OutgoingPayload
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

    def run(self, context, incoming, record):
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


def make_pipeline(snapshot):
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
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_message_pipeline.py
```

預期：FAIL，包含 `ModuleNotFoundError: No module named 'multi_profile.message_pipeline'`。

- [ ] **步驟 3：實作 MultiProfilePipeline**

建立 `multi_profile/message_pipeline.py`：

```python
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

from adapters.base import IncomingMessage, OutgoingPayload
from adapters.feishu import extract_file_paths

from .models import ExecutionContext
from .registry import ConfigRegistry
from .router import RouteNotFound, TenantRouter
from .runtime import ContextRuntime
from .scoped_state import semantic_owner
from .session_store import SessionStore

log = logging.getLogger("multi-profile-pipeline")

_UNMAPPED_GROUP_REPLY = (
    "⚠️ 本群尚未配置執行環境（未映射 profile）。"
    "請聯繫管理員在 Dashboard 設定群路由後再試。"
)
_UNMAPPED_GROUP_ALERT_REPLY = (
    "⚠️ 檢測到告警 [{title}]，但本群尚未配置告警分析環境，已跳過分析。"
)
_PROFILE_UNAVAILABLE_REPLY = "⚠️ 本群綁定的執行環境目前不可用，請聯繫管理員。"
_PRIVATE_ROUTE_FAILED_REPLY = "⚠️ 此 App 的預設執行環境不可用，請聯繫管理員。"


class MultiProfilePipeline:
    """多 profile 訊息管線：TenantRouter 路由 + fail-closed + ContextRuntime 執行。

    每則訊息只取一次 snapshot；所有回覆都經原 App 的 adapter。
    """

    def __init__(
        self,
        *,
        registry: ConfigRegistry,
        dispatcher,
        runtime: ContextRuntime,
        session_store: SessionStore,
        alert_runner,
        parse_alert: Callable[[str], dict | None],
        memory=None,
        build_prompt_fn: Callable | None = None,
        auto_analyze_severities: tuple[str, ...] | None = None,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ):
        self._registry = registry
        self._dispatcher = dispatcher
        self._runtime = runtime
        self._session_store = session_store
        self._alert_runner = alert_runner
        self._parse_alert = parse_alert
        self._memory = memory
        self._build_prompt = build_prompt_fn or (lambda text, sem, epi: text)
        if auto_analyze_severities is None:
            auto_analyze_severities = tuple(
                s.strip()
                for s in os.environ.get(
                    "ALERT_AUTO_ANALYZE_SEVERITY", "high,critical"
                ).split(",")
                if s.strip()
            )
        self._auto_severities = auto_analyze_severities
        self._thread_factory = thread_factory

    # ---- 入口 ----

    def handle(self, incoming: IncomingMessage) -> None:
        snapshot = self._registry.snapshot()  # 每則訊息只取一次
        router = TenantRouter(snapshot)
        text = (incoming.text or "").strip()
        if not text:
            return

        alert_record = (
            self._parse_alert(text) if incoming.chat_type == "group" else None
        )

        try:
            context = router.resolve(
                platform=incoming.platform,
                app_key=incoming.app_key,
                chat_type=incoming.chat_type,
                chat_id=incoming.group_id,
                user_id=incoming.raw_user_id,
            )
        except RouteNotFound as exc:
            self._handle_route_not_found(incoming, alert_record, exc)
            return

        if alert_record is not None:
            self._handle_group_alert(context, incoming, alert_record)
            return

        if incoming.chat_type == "group" and not incoming.is_at_me:
            return  # 已映射群的非 @ 普通訊息：靜默

        self._handle_chat(context, incoming, text)

    # ---- fail-closed ----

    def _handle_route_not_found(
        self,
        incoming: IncomingMessage,
        alert_record: dict | None,
        error: RouteNotFound,
    ) -> None:
        log.warning(
            f"路由失敗 app={incoming.app_key} chat={incoming.group_id} "
            f"type={incoming.chat_type}: {error}"
        )
        if incoming.chat_type == "group":
            if alert_record is not None:
                self._reply(
                    incoming,
                    _UNMAPPED_GROUP_ALERT_REPLY.format(title=alert_record["title"]),
                )
            elif incoming.is_at_me:
                self._reply(incoming, _UNMAPPED_GROUP_REPLY)
            # 未 @ 且非告警：靜默
        else:
            self._reply(incoming, _PRIVATE_ROUTE_FAILED_REPLY)
        # 任何情況都不啟動 Kiro／AWS 子程序，不使用 fallback profile

    # ---- 群告警 ----

    def _handle_group_alert(
        self,
        context: ExecutionContext,
        incoming: IncomingMessage,
        record: dict,
    ) -> None:
        should_analyze = record.get("severity", "") in self._auto_severities
        self._thread_factory(
            target=self._alert_runner.run,
            args=(context, incoming, record, should_analyze),
            daemon=True,
            name=f"mp-group-alert-{context.app_key}-{record['title'][:20]}",
        ).start()
        if should_analyze:
            self._reply(
                incoming,
                f"🚨 检测到告警 [{record['title']}]，分析中，请稍候...",
            )

    # ---- 普通對話與命令 ----

    def _handle_chat(self, context: ExecutionContext, incoming: IncomingMessage, text: str) -> None:
        principal = context.principal_key

        if text == "/new":
            self._session_store.clear_active(principal)
            self._reply(incoming, "🆕 已切换到新会话模式，下条消息将开启新对话。")
            return
        if text == "/status":
            status = self._runtime.status(context)
            self._reply(incoming, status or "没有正在运行的后台任务。")
            return
        if text == "/cancel":
            self._reply(
                incoming,
                "⏹ 已取消当前任务。" if self._runtime.cancel(context) else "没有正在运行的后台任务。",
            )
            return
        if text.startswith("/sessions") or text.startswith("/resume"):
            self._reply(incoming, self._session_store.list_sessions(context))
            return

        if self._runtime.is_busy(context):
            self._reply(incoming, "⏳ 上一个任务还在后台运行中，请等待完成或发送 /cancel 取消。")
            return

        self._reply(incoming, "🤖 正在处理，请稍候...")

        semantic_memories: list[str] = []
        if self._memory is not None:
            try:
                owner = semantic_owner(context)
                self._memory.add(owner, f"用户说：{text}")
                semantic_memories = self._memory.search(owner, text)
            except Exception:
                log.exception(f"記憶檢索失敗 principal={principal}，繼續執行")

        prompt = self._build_prompt(text, semantic_memories, [])

        def on_sync_result(output: str):
            self._reply(incoming, output)

        def on_async_start():
            self._reply(
                incoming,
                "⏳ 任务较复杂，已转入后台处理。完成后会主动推送结果。\n发送 /status 查看进度，/cancel 取消。",
            )

        def on_async_result(output: str):
            self._reply(incoming, output)

        def on_error(failure):
            self._reply(incoming, f"❌ 任务失败（{failure.code}）：{failure.message}")

        def on_progress(message: str):
            self._reply(incoming, message)

        self._runtime.execute(
            context,
            prompt,
            on_sync_result=on_sync_result,
            on_async_start=on_async_start,
            on_async_result=on_async_result,
            on_error=on_error,
            on_progress=on_progress,
        )

    # ---- 回覆（永遠經原 App） ----

    def _reply(self, incoming: IncomingMessage, text: str) -> None:
        adapter = self._dispatcher.get_adapter(incoming.platform, incoming.app_key)
        if adapter is None:
            log.error(
                f"找不到适配器: {incoming.platform}/{incoming.app_key}，無法回覆"
            )
            return
        images, files = extract_file_paths(text)
        adapter.reply(incoming, OutgoingPayload(text=text.strip(), images=images, files=files))
```

`/resume` 的逐條互動（`list_sessions` 回傳含 short_id 的清單文字、`get_by_short_id` + fingerprint 檢查）沿用計畫 2 的 `SessionStore` 介面；本計畫管線先把 `/resume` 導向清單，細部互動文字在整合測試（任務 8）中驗證。若要完整對齊 legacy `/resume <編號>` 行為，在 `_handle_chat` 中比照 `message_handler.py` 解析編號並呼叫 `session_store.get_by_short_id(context, short_id)`，fingerprint 不符時 `SessionStore` 依計畫 2 行為拒絕恢復。

- [ ] **步驟 4：修改 MessageHandler 委派**

修改 `message_handler.py` 建構子與 `handle()` 開頭（其餘完全不動）：

```python
class MessageHandler:
    def __init__(self, dispatcher: PlatformDispatcher, mp_pipeline=None):
        self.dispatcher = dispatcher
        self._mp_pipeline = mp_pipeline
        self.session_router = SessionRouter(kiro_bin=kiro_bin, kiro_agent=KIRO_AGENT)
        self.kiro_executor = KiroExecutor(agent=KIRO_AGENT)
        self.scheduler = Scheduler(
            send_fn=self._send_to_target,
            kiro_fn=self._call_kiro_simple,
        )

    def handle(self, incoming: IncomingMessage) -> None:
        """所有平台消息的统一入口."""
        # 多 profile 模式：飛書訊息全部交由 MultiProfilePipeline（含告警與命令）
        if self._mp_pipeline is not None and incoming.platform == "feishu":
            self._mp_pipeline.handle(incoming)
            return
        # ====== 以下為 legacy 路徑，行為不變 ======
        user_id = incoming.unified_user_id
        ...
```

微信訊息即使多 profile 模式啟用仍走 legacy 路徑（微信多 App 是非目標，規格 §3）。

- [ ] **步驟 5：執行管線測試與 MessageHandler 回歸**

```bash
pytest -q \
  tests/test_multi_profile_message_pipeline.py \
  tests/test_group_alert_detection.py \
  tests/test_platform_dispatcher.py
```

預期：全部 PASS；舊群告警測試（`MessageHandler(dispatcher=MagicMock())`，`mp_pipeline` 預設 None）行為不變。

在 `multi_profile/__init__.py` 追加：

```python
from .message_pipeline import MultiProfilePipeline

__all__ += ["MultiProfilePipeline"]
```

- [ ] **步驟 6：提交任務 5**

```bash
git add multi_profile/message_pipeline.py message_handler.py multi_profile/__init__.py tests/test_multi_profile_message_pipeline.py
git commit -m "feat(多租戶): 訊息管線 fail-closed 路由與 ContextRuntime 接線"
```

---

### 任務 6：群告警 ExecutionContext 與原 App 回覆

**文件：**
- 建立：`multi_profile/group_alerts.py`
- 建立：`tests/test_multi_profile_group_alerts.py`
- 修改：`alert_analysis.py`
- 修改：`multi_profile/__init__.py`

設計要點（對應規格 §12、§15.1、§15.2）：

- 解析、去重與 Alert Mapping 沿用既有機制；新增 `ExecutionContext` 維度。
- 去重鍵與事件入庫使用 `group_scope_key`（同一外部告警在不同 App／群各自入庫）。
- Agent 優先順序：**Alert Mapping action → profile `alert_agent` → 全域預設 `ec2-alert-analyzer`**。
- 模型優先順序：**profile `alert_model` → profile `model` → `BACKGROUND_MODEL`**。
- AWS profile／Region **只能**來自 `ExecutionContext`（`build_child_env(context)`）；Alert Mapping 回傳的 action 中即使夾帶 `aws_profile`、`env` 等鍵也必須被忽略。
- 分析逾時：Alert Mapping action → profile `alert_timeout` → `ALERT_ANALYZE_TIMEOUT` 預設。
- 回覆經原 `IncomingMessage` 由原 App 回原群；錯誤在原群回覆摘要＋trace ID，**不改用其他 profile 重試、不 fallback**。
- 告警分析不加入普通聊天 Session：不觸碰 `SessionStore`、不帶 `--resume-id`。

- [ ] **步驟 1：編寫失敗的群告警測試**

建立 `tests/test_multi_profile_group_alerts.py`：

```python
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
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_group_alerts.py
```

預期：FAIL，包含 `ModuleNotFoundError: No module named 'multi_profile.group_alerts'`，且 `run_alert_analysis` 尚不接受 `context`。

- [ ] **步驟 3：實作 group_alerts**

建立 `multi_profile/group_alerts.py`：

```python
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from adapters.base import IncomingMessage, OutgoingPayload
from adapters.feishu import extract_file_paths
from alert_analysis import run_alert_analysis

from .models import ExecutionContext, ProfileConfig
from .scoped_state import event_owner, scoped_event_id

log = logging.getLogger("multi-profile-group-alerts")


@dataclass(frozen=True)
class AlertResolution:
    """Alert Mapping 與 profile 合併後的執行參數。刻意不含任何 AWS 欄位：
    AWS profile／Region 只能來自 ExecutionContext（規格 §12.6）。"""

    agent: str
    model: str | None
    tools: tuple[str, ...]
    timeout: int


def resolve_alert_action(
    action: Mapping,
    profile: ProfileConfig,
    *,
    default_agent: str,
    default_tools: list[str],
    default_timeout: int,
    background_model: str,
) -> AlertResolution:
    """優先順序（規格 §12）：

    - Agent：Alert Mapping action → profile alert_agent → 全域預設
    - 模型：profile alert_model → profile model → BACKGROUND_MODEL
    - 逾時：Alert Mapping action → profile alert_timeout → 全域預設
    - AWS 身分：不在此解析，永遠來自 ExecutionContext
    """
    agent = action.get("agent") or profile.alert_agent or default_agent
    model = profile.alert_model or profile.model or background_model or None
    tools = tuple(action.get("tools") or default_tools)
    timeout = action.get("timeout") or profile.alert_timeout or default_timeout
    return AlertResolution(agent=agent, model=model, tools=tools, timeout=timeout)


class GroupAlertRunner:
    """群告警：scope 去重 → 事件入庫 → （高級別）分析 → 原 App 原群回覆。"""

    def __init__(
        self,
        *,
        dispatcher,
        event_store=None,
        run_analysis: Callable[..., tuple[str, str]] = run_alert_analysis,
        dedup_window_sec: int = 300,
        trace_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex[:12],
        clock: Callable[[], float] = time.time,
    ):
        self._dispatcher = dispatcher
        self._event_store = event_store
        self._run_analysis = run_analysis
        self._dedup_window = dedup_window_sec
        self._trace_id_factory = trace_id_factory
        self._clock = clock
        self._dedup: dict[tuple[str, str], float] = {}

    @staticmethod
    def dedup_key(context: ExecutionContext, record: dict) -> tuple[str, str]:
        return (context.group_scope_key or context.principal_key, record.get("title", ""))

    def _is_duplicate(self, context: ExecutionContext, record: dict) -> bool:
        key = self.dedup_key(context, record)
        now = self._clock()
        if now - self._dedup.get(key, 0) < self._dedup_window:
            log.info(f"群告警去重({self._dedup_window}s): {key}")
            return True
        self._dedup[key] = now
        return False

    def run(
        self,
        context: ExecutionContext,
        incoming: IncomingMessage,
        record: dict,
        should_analyze: bool,
    ) -> None:
        trace_id = self._trace_id_factory()
        log.info(
            f"群告警 trace={trace_id} app={context.app_key} chat={context.chat_id} "
            f"profile={context.profile_id} gen={context.config_generation} "
            f"title={record.get('title')} analyze={should_analyze}"
        )
        if self._is_duplicate(context, record):
            return
        self._ingest(context, incoming, record)
        if not should_analyze:
            return

        try:
            message, agent = self._run_analysis(record, context=context)
        except Exception as e:
            # 規格 §15.1：原群回覆錯誤摘要與 trace ID；不 fallback、不改用其他 profile
            log.exception(f"群告警分析失敗 trace={trace_id}")
            self._reply(
                incoming,
                f"❌ 告警分析失敗：{type(e).__name__}: {e}\n（trace: {trace_id}）",
            )
            return
        self._reply(incoming, message)

    def _ingest(
        self,
        context: ExecutionContext,
        incoming: IncomingMessage,
        record: dict,
    ) -> None:
        if self._event_store is None:
            return
        try:
            from event_ingest import ingest_to_store

            store_record = {
                # scoped_event_id 讓同一外部告警在不同 App／群各自入庫（規格 §8、§11）
                "event_id": scoped_event_id(
                    context, f"group-{incoming.platform}-{incoming.message_id}"
                ),
                "user_id": event_owner(context),
                "title": record["title"],
                "description": record.get("description", ""),
                "event_type": record.get("event_type", "指标异常"),
                "entities": record.get("entities", []),
                "source": record.get("source", "prometheus"),
                "severity": record.get("severity", "medium"),
                "timestamp": record.get("timestamp"),
            }
            result = ingest_to_store(self._event_store, store_record)
            if not result["ok"]:
                log.warning(f"群告警入庫失敗: {result.get('error')}")
        except Exception:
            log.exception("群告警入庫異常")

    def _reply(self, incoming: IncomingMessage, text: str) -> None:
        adapter = self._dispatcher.get_adapter(incoming.platform, incoming.app_key)
        if adapter is None:
            # 規格 §15.2：記錄 App/chat/trace，但不得改由另一 App 發送
            log.error(
                f"找不到适配器 {incoming.platform}/{incoming.app_key}，群告警回覆失敗"
            )
            return
        images, files = extract_file_paths(text)
        adapter.reply(incoming, OutgoingPayload(text=text.strip(), images=images, files=files))
```

- [ ] **步驟 4：修改 alert_analysis 支援 context**

修改 `alert_analysis.py` 的 `run_alert_analysis`（其餘函式不動）：

```python
def run_alert_analysis(record: dict, context=None) -> tuple[str, str]:
    """触发 Kiro skill 分析并返回结果文本和使用的 agent.

    Args:
        record: 标准化告警记录，至少包含 title、severity、source 等字段。
        context: 多 profile 模式的 ExecutionContext；None 時維持 legacy 全域行為。

    Returns:
        (analysis_message, agent_name)
    """
    matcher = config_reloader.get_matcher()
    action = matcher.match(record)

    if context is not None:
        # 多 profile：Agent／模型／逾時依規格 §12 優先順序；
        # AWS 只來自 ExecutionContext（Alert Mapping 不得覆蓋）
        from multi_profile.group_alerts import resolve_alert_action
        from multi_profile.runtime_env import build_child_env

        resolved = resolve_alert_action(
            action,
            context.profile,
            default_agent=DEFAULT_AGENT,
            default_tools=DEFAULT_TOOLS,
            default_timeout=DEFAULT_TIMEOUT,
            background_model=os.environ.get("BACKGROUND_MODEL", "").strip(),
        )
        agent, tools, timeout = resolved.agent, list(resolved.tools), resolved.timeout
        model = resolved.model
        env = build_child_env(context)
        cwd = context.profile.working_dir
    else:
        # legacy：全域行為不變
        agent = action.get("agent", DEFAULT_AGENT)
        tools = action.get("tools", DEFAULT_TOOLS)
        timeout = action.get("timeout", DEFAULT_TIMEOUT)
        model = os.environ.get("BACKGROUND_MODEL", "").strip() or None
        env = {**os.environ, "NO_COLOR": "1"}
        cwd = os.path.expanduser("~")

    instruction = action.get("instruction")
    if not instruction:
        instruction = "请分析此告警的根因，查询相关指标数据，给出结构化的诊断报告。"

    alert_payload = json.dumps({
        "alert": {
            "source": record.get("source", "prometheus"),
            "event_type": record.get("event_type", "指标异常"),
            "title": record["title"],
            "description": record.get("description", ""),
            "entities": record.get("entities", []),
            "severity": record.get("severity", "medium"),
            "timestamp": record.get("timestamp"),
        },
        "instruction": instruction,
    }, ensure_ascii=False, indent=2)

    log.info(f"触发 Kiro {agent}: {record['title'][:50]}...")
    cmd = [KIRO_BIN, "chat", "--no-interactive", "-a", "--wrap", "never"]
    for tool in tools:
        cmd.append(f"--trust-tools={tool}")
    cmd += ["--agent", agent]
    if model:
        cmd += ["--model", model]
    cmd.append(alert_payload)

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=cwd, env=env,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        analysis = strip_ansi(stdout.strip() or stderr.strip() or "Kiro 未返回分析结果")
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()
        except Exception:
            pass
        analysis = f"⏰ Kiro {agent} 分析超时（{timeout}s）"
    except Exception as e:
        analysis = f"❌ Kiro 调用失败: {e}"
        log.exception("Kiro 分析失败")

    header = (
        f"🚨 自动告警分析\n\n"
        f"【告警】{record['title']}\n"
        f"【级别】{record.get('severity', 'medium').upper()}\n"
        f"【来源】{record.get('source', 'prometheus')}\n"
    )
    message = header + "\n" + analysis
    return message, agent
```

注意：此處在函式內延遲匯入 `multi_profile`，避免 legacy 部署（尚未有 `multi_profile` package 的舊版 checkout 回滾情境）匯入失敗。

- [ ] **步驟 5：執行群告警測試與 legacy 告警回歸**

```bash
pytest -q \
  tests/test_multi_profile_group_alerts.py \
  tests/test_group_alert_detection.py \
  tests/test_multi_profile_message_pipeline.py
```

預期：全部 PASS。

在 `multi_profile/__init__.py` 追加：

```python
from .group_alerts import AlertResolution, GroupAlertRunner, resolve_alert_action

__all__ += ["AlertResolution", "GroupAlertRunner", "resolve_alert_action"]
```

- [ ] **步驟 6：提交任務 6**

```bash
git add multi_profile/group_alerts.py alert_analysis.py multi_profile/__init__.py tests/test_multi_profile_group_alerts.py
git commit -m "feat(多租戶): 群告警使用 ExecutionContext 並原 App 回覆"
```

---

### 任務 7：gateway 雙模式啟動接線

**文件：**
- 修改：`gateway.py`
- 建立：`tests/test_multi_profile_gateway_boot.py`

設計要點（對應規格 §15.3、§18）：

- `MULTI_PROFILE_ENABLED=false`（或不存在）：走現有 legacy 路徑，一個 `FeishuAdapter`、讀 `FEISHU_APP_ID`／`FEISHU_APP_SECRET`／`FEISHU_POLL_CHAT_IDS`；本計畫對此路徑零行為變更。
- `MULTI_PROFILE_ENABLED=true`：建立 `ConfigRegistry` → `SessionStore`／`TaskRegistry`／`SessionCaptureCoordinator`／`ContextRuntime` → `GroupAlertRunner` → `MultiProfilePipeline` → `MessageHandler(mp_pipeline=...)` → `AppManager.start_all()`。**不得**同時建立 legacy `FeishuAdapter`，避免重複 WebSocket（規格 §18）。
- 設定無效且無法載入：gateway 記錄錯誤，飛書多 profile 執行停用，但微信與 Webhook 仍啟動；**不得**自動退回 legacy profile（規格 §15.3）。
- 把 `main()` 拆成可注入依賴的 `_run_legacy()`／`_run_multi_profile()`，讓測試不必啟動真實執行緒。

- [ ] **步驟 1：編寫失敗的啟動測試**

建立 `tests/test_multi_profile_gateway_boot.py`：

```python
import threading
from unittest.mock import MagicMock

import pytest

import gateway


@pytest.fixture(autouse=True)
def no_threads(monkeypatch):
    """所有模式都不真的啟動執行緒或 webhook。"""
    monkeypatch.setattr(gateway.threading, "Thread", lambda **kw: MagicMock(start=lambda: None))
    monkeypatch.setattr(gateway, "start_webhook_server", lambda *a, **kw: None)


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

    class FakeFeishu:
        platform = "feishu"

        def __init__(self, **kw):
            self.app_key = kw.get("app_key", "default")

        def start(self):
            pass

        def stop(self):
            pass

    legacy_feishu = MagicMock(side_effect=AssertionError("legacy adapter must not be built"))
    monkeypatch.setattr(gateway, "FeishuAdapter", legacy_feishu)
    monkeypatch.setattr(gateway, "WeixinAdapter", MagicMock(platform="weixin"))

    dispatcher = gateway.build_gateway()

    legacy_feishu.assert_not_called()  # 無重複 WS 連線
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
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_gateway_boot.py
```

預期：FAIL，`gateway` 沒有 `build_gateway`、`is_enabled`、`AppManager` 等名稱。

- [ ] **步驟 3：重構 gateway.py**

將 `gateway.py` 完整替換為：

```python
#!/usr/bin/env python3
"""kiro-devops 统一入口 — 同时运行飞书（單 App legacy 或多 App）、微信、Webhook 通道."""
import logging
import os
import sys
import threading

from dotenv import load_dotenv
load_dotenv()

from adapters import FeishuAdapter, WeixinAdapter
from message_handler import MessageHandler
from platform_dispatcher import PlatformDispatcher
from webhook_server import start_webhook_server

from multi_profile import (
    AppManager,
    ConfigRegistry,
    ContextRuntime,
    GroupAlertRunner,
    MultiProfilePipeline,
    SessionCaptureCoordinator,
    SessionStore,
    TaskRegistry,
    config_path,
    is_enabled,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("gateway")

APP_ID = os.environ.get("FEISHU_APP_ID", "").strip()
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "").strip()
WEIXIN_BOT_TOKEN = os.environ.get("WEIXIN_BOT_TOKEN", "").strip() or None
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
TENANT_SESSION_DB = os.path.join(PROJECT_DIR, "runtime", "tenant_sessions.db")


def _start_thread(target, name):
    t = threading.Thread(target=target, name=name, daemon=True)
    t.start()
    return t


def _start_weixin(dispatcher, handler, threads):
    weixin = WeixinAdapter(bot_token=WEIXIN_BOT_TOKEN, on_message=handler.handle)
    dispatcher.register(weixin)
    threads.append(_start_thread(weixin.start, "weixin-poll"))
    log.info("✅ 微信适配器已启动")


def _maybe_start_webhook(handler):
    if os.environ.get("WEBHOOK_ENABLED", "false").lower() == "true":
        port = int(os.environ.get("WEBHOOK_PORT", "8080"))
        host = os.environ.get("WEBHOOK_HOST", "127.0.0.1")
        start_webhook_server(handler, host=host, port=port)
    else:
        log.info("🌐 Webhook 未启用")


def _build_multi_profile_handler(dispatcher):
    """建立多 profile 模式的 handler 與 AppManager。設定無效時拋出例外。"""
    registry = ConfigRegistry(config_path(project_dir=PROJECT_DIR))
    registry.load_initial()

    session_store = SessionStore(TENANT_SESSION_DB)
    runtime = ContextRuntime(
        kiro_bin=os.environ.get("KIRO_BIN", "").strip() or "kiro-cli",
        session_store=session_store,
        session_capture=SessionCaptureCoordinator(),
        task_registry=TaskRegistry(),
    )
    alert_runner = GroupAlertRunner(dispatcher=dispatcher)
    pipeline = MultiProfilePipeline(
        registry=registry,
        dispatcher=dispatcher,
        runtime=runtime,
        session_store=session_store,
        alert_runner=alert_runner,
        parse_alert=None,  # 由下方 MessageHandler 建立後補上，避免循環依賴
    )
    handler = MessageHandler(dispatcher=dispatcher, mp_pipeline=pipeline)
    pipeline._parse_alert = handler._parse_structured_alert  # 沿用既有告警解析
    app_manager = AppManager(
        registry=registry,
        adapter_factory=lambda **kw: FeishuAdapter(**kw),
        dispatcher=dispatcher,
        on_message=handler.handle,
    )
    return handler, app_manager


def build_gateway():
    """依 MULTI_PROFILE_ENABLED 建立 dispatcher/handler/執行緒；回傳 dispatcher（供測試）。"""
    dispatcher = PlatformDispatcher()
    threads = []

    if is_enabled():
        try:
            handler, app_manager = _build_multi_profile_handler(dispatcher)
        except Exception:
            # 規格 §15.3：設定無效時停用多 profile 訊息執行，
            # 但不得自動退回 legacy profile；其餘通道照常啟動
            log.exception(
                "❌ 多 profile 設定載入失敗；飛書多 App 執行停用（不退回 legacy）"
            )
            handler = MessageHandler(dispatcher=dispatcher)
            _start_weixin(dispatcher, handler, threads)
            _maybe_start_webhook(handler)
            return dispatcher

        app_manager.start_all()
        _start_weixin(dispatcher, handler, threads)
        _maybe_start_webhook(handler)
        log.info("🚀 kiro-devops gateway 启动完成（multi-profile 模式）")
        _keep_alive(threads)
        return dispatcher

    # ====== legacy 路徑：行為與重構前完全相同 ======
    handler = MessageHandler(dispatcher=dispatcher)

    if APP_ID and APP_SECRET:
        feishu = FeishuAdapter(
            app_id=APP_ID,
            app_secret=APP_SECRET,
            on_message=handler.handle,
        )
        dispatcher.register(feishu)
        threads.append(_start_thread(feishu.start, "feishu-ws"))
        log.info("✅ 飞书适配器已启动")
    else:
        log.warning("⚠️  FEISHU_APP_ID / FEISHU_APP_SECRET 未设置，跳过飞书")

    _start_weixin(dispatcher, handler, threads)
    _maybe_start_webhook(handler)
    log.info("🚀 kiro-devops gateway 启动完成")
    _keep_alive(threads)
    return dispatcher


def _keep_alive(threads):
    try:
        while True:
            for t in threads:
                t.join(timeout=1)
    except KeyboardInterrupt:
        log.info("👋 收到退出信号，正在关闭...")
        sys.exit(0)


def main():
    build_gateway()


if __name__ == "__main__":
    main()
```

測試替身把 `_keep_alive` 變成不會真的 join——在 `no_threads` fixture 中加 `monkeypatch.setattr(gateway, "_keep_alive", lambda threads: None)`。

- [ ] **步驟 4：執行啟動測試**

```bash
pytest -q tests/test_multi_profile_gateway_boot.py
```

預期：3 passed。

- [ ] **步驟 5：確認 legacy 行為的靜態等價**

```bash
git diff .git/plan3-base-sha -- gateway.py
python3 - <<'PY'
# 靜態檢查：legacy 分支只做「原 main() 內容搬移」，不改變任何 env 讀取或註冊順序
import inspect, gateway
src = inspect.getsource(gateway.build_gateway)
assert src.index("APP_ID and APP_SECRET") > src.index("legacy 路徑")
print("legacy branch preserved")
PY
```

預期：diff 顯示 legacy 分支為原邏輯搬移；輸出 `legacy branch preserved`。

- [ ] **步驟 6：提交任務 7**

```bash
git add gateway.py tests/test_multi_profile_gateway_boot.py
git commit -m "feat(多租戶): gateway 雙模式啟動與多 App 接線"
```

---

### 任務 8：多 App 整合與故障隔離測試（規格 §21.2）

**文件：**
- 建立：`tests/test_multi_profile_integration_apps.py`

- [ ] **步驟 1：編寫整合測試**

建立 `tests/test_multi_profile_integration_apps.py`，使用真實 `ConfigRegistry`＋`TenantRouter`＋`PlatformDispatcher`＋`MultiProfilePipeline`，以 Fake Adapter 與 Fake Runtime 隔離外部程序：

```python
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
        time.sleep(0.3)
        status = manager.status()
        assert status["app-b"]["state"] == AppConnState.CONNECTED.value
        assert status["app-a"]["state"] in (
            AppConnState.RECONNECTING.value, AppConnState.DISCONNECTED.value
        )
        assert sleeps and sleeps[0] == 1  # app-a 在退避重連
    finally:
        manager.stop_all()


def test_poll_sets_follow_snapshot_per_app(stack):
    _, _, manager, adapters, _, _ = stack
    assert adapters["app-a"].kw["poll_chat_ids"]() == ["oc_shared"]
    assert adapters["app-b"].kw["poll_chat_ids"]() == ["oc_shared"]
```

- [ ] **步驟 2：執行整合測試並修正**

```bash
pytest -q tests/test_multi_profile_integration_apps.py
```

預期：全部 PASS。若失敗，依 systematic-debugging 定位，不得以放寬斷言收場。

- [ ] **步驟 3：提交任務 8**

```bash
git add tests/test_multi_profile_integration_apps.py
git commit -m "test(多租戶): 多 App 整合與故障隔離測試"
```

---

### 任務 9：計畫級完整驗證與公開介面

**文件：**
- 修改：`.env.example`（補充多 App 憑證命名範例）
- 不新增程式檔；驗證任務 1–8 的結果。

- [ ] **步驟 1：補充 `.env.example`**

在計畫 1 加入的 multi-profile 段落之後追加：

```dotenv
# 多 App 模式：每個 App 的憑證仍是獨立 env 變數，YAML 只引用名稱
# FEISHU_OPS_APP_ID=cli_xxxxxxxxxxxxxxxx
# FEISHU_OPS_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# FEISHU_TRADE_APP_ID=cli_yyyyyyyyyyyyyyyy
# FEISHU_TRADE_APP_SECRET=yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy
```

- [ ] **步驟 2：執行計畫 3 targeted tests**

```bash
pytest -q \
  tests/test_multi_profile_dispatcher.py \
  tests/test_multi_profile_feishu_adapter.py \
  tests/test_multi_profile_poll_sets.py \
  tests/test_multi_profile_app_manager.py \
  tests/test_multi_profile_message_pipeline.py \
  tests/test_multi_profile_group_alerts.py \
  tests/test_multi_profile_gateway_boot.py \
  tests/test_multi_profile_integration_apps.py
```

預期：全部 PASS，0 failed。

- [ ] **步驟 3：執行計畫 1–3 全部多 profile 測試**

```bash
pytest -q tests/test_multi_profile_*.py
```

預期：全部 PASS。

- [ ] **步驟 4：執行 legacy 行為回歸**

```bash
pytest -q \
  tests/test_platform_dispatcher.py \
  tests/test_group_alert_detection.py \
  tests/test_adapters_weixin.py \
  tests/test_dashboard_api_events.py
```

預期：全部 PASS；單 App Dispatcher、legacy 群告警流程、微信適配器與 Dashboard 事件行為不變。

- [ ] **步驟 5：執行完整測試套件**

```bash
pytest -q
```

預期：0 failed。若出現與本計畫無關的既有失敗，停止並依 systematic-debugging 確認基線，不得忽略。

- [ ] **步驟 6：執行 Python 編譯檢查**

```bash
python3 -m compileall -q multi_profile adapters platform_dispatcher.py message_handler.py alert_analysis.py gateway.py tests
```

預期：exit 0。

- [ ] **步驟 7：確認計畫 4 可依賴的公開介面可匯入**

```bash
python3 - <<'PY'
from multi_profile import (
    AlertResolution,
    AppConnState,
    AppManager,
    GroupAlertRunner,
    MultiProfilePipeline,
    poll_chat_ids_for_app,
    resolve_alert_action,
)
print("plan 3 public API import OK")
PY
```

預期：輸出 `plan 3 public API import OK`。

- [ ] **步驟 8：確認未修改的範圍**

```bash
PLAN3_BASE_SHA=$(cat .git/plan3-base-sha)
git diff "${PLAN3_BASE_SHA}"..HEAD -- \
  kiro_executor.py session_router.py memory.py semantic_store.py \
  event_store.py scheduler.py webhook_server.py adapters/weixin.py \
  adapters/weixin_media.py dashboard \
  multi_profile/models.py multi_profile/config_loader.py \
  multi_profile/registry.py multi_profile/router.py \
  multi_profile/runtime.py multi_profile/runtime_env.py \
  multi_profile/session_store.py multi_profile/session_capture.py \
  multi_profile/task_registry.py multi_profile/scoped_state.py \
  multi_profile/output.py multi_profile/process_utils.py
```

預期：沒有輸出（`multi_profile/__init__.py` 的匯出追加除外，不列於此處）。

- [ ] **步驟 9：確認 legacy 模式預設且日誌無 Secret**

```bash
python3 - <<'PY'
from multi_profile import is_enabled
assert is_enabled({}) is False
print("legacy mode remains the default")
PY

grep -rn "app_secret" multi_profile/ --include="*.py" | grep -v "app_secret_env" || echo "no secret value handling in multi_profile"
```

預期：第一段輸出 `legacy mode remains the default`；第二段只輸出 `no secret value handling in multi_profile`（AppManager 只經 env 名稱取值，不落日誌）。

- [ ] **步驟 10：確認工作區與提交範圍**

```bash
git status --short
PLAN3_BASE_SHA=$(cat .git/plan3-base-sha)
git log --oneline "${PLAN3_BASE_SHA}"..HEAD
```

預期：沒有未提交的本計畫檔案；列出 8 筆任務提交。

---

## 完成標準

- `IncomingMessage` 帶 `app_key`（預設 `default`）；`PlatformDispatcher` 以 `(platform, app_key)` 註冊查找，legacy `platform:raw_id` 行為不變。
- `FeishuAdapter` 可多實例化：per-instance 去重、注入式輪詢集合（list 或 callable）、`group_alert_listen` 覆寫、`on_disconnected` 回呼與 `stop()`。
- `AppManager` 依 snapshot 建立並監督所有啟用 App；單 App 崩潰不影響其他 App；退避序列 `1,2,4,8,16,32,60` 上限 60 秒，穩定連線後重設；連線欄位變更或新 App 標記 `pending-restart`，不自動連線。
- 輪詢群集合由 snapshot 中 `poll_alerts: true` 路由動態決定，熱載入後下一輪生效；單群輪詢錯誤只標記該群。
- `MessageHandler` 在管線存在時把飛書訊息委派給 `MultiProfilePipeline`：群以 `(app_key, chat_id)`、私聊以 App `default_profile` 路由；未映射群 @Bot／可辨識告警明確拒絕、非 @ 非告警靜默；所有 fail-closed 路徑不啟動任何子程序。
- 群告警：`group_scope_key` 去重與入庫；Agent（Mapping→profile `alert_agent`→預設）與模型（`alert_model`→`model`→`BACKGROUND_MODEL`）優先順序正確；AWS 只來自 `ExecutionContext`；回覆經原 App 原群；錯誤附 trace ID 且不 fallback；不觸碰聊天 Session。
- `gateway.py`：`MULTI_PROFILE_ENABLED=false` 時行為與重構前完全一致；`true` 時只建立多 App 路徑，無重複 WS；設定無效時停用飛書執行但不退回 legacy。
- Targeted、計畫 1–3、legacy、完整 pytest 與 compileall 全部通過。

## 不在本計畫範圍

- 不實作 STS 健康檢查、`ProfileHealthMonitor`、`/profile` 命令或 profile `blocked` 狀態攔截（計畫 4）。
- 不實作 Dashboard API／UI、Draft 驗證、原子發布、revision、last-known-good 或設定回滾（計畫 4）；`AppManager.on_snapshot_changed` 僅提供計畫 4 呼叫的掛點。
- 不執行遷移、release manifest、dark deployment、回滾演練或規模測試（計畫 5）。
- 不修改微信的多 App 或 AWS profile 路由（規格 §3 非目標）。
- 不修改 Scheduler／Webhook 的 profile 路由；其在多 profile 模式下的行為維持 legacy。
- 不啟用 `MULTI_PROFILE_ENABLED=true`；正式切流只允許在計畫 5。

## 計畫 4 可依賴的公開介面

```python
from multi_profile import (
    AlertResolution,
    AppConnState,
    AppManager,
    GroupAlertRunner,
    MultiProfilePipeline,
    poll_chat_ids_for_app,
    resolve_alert_action,
)
```

計畫 4 的 Dashboard 狀態端點應使用 `AppManager.status()` 回報各 App 的 `connected/disconnected/reconnecting/pending-restart`；熱載入後呼叫 `AppManager.on_snapshot_changed(new_snapshot)` 標記 `pending-restart`，不得自行重建 Adapter。

## 回滾說明

- **功能回滾（首選）：** 設定 `MULTI_PROFILE_ENABLED=false` 並重啟服務。legacy 啟動分支在本計畫中只搬移未改邏輯，並由 `tests/test_multi_profile_gateway_boot.py::test_legacy_mode_builds_single_default_app_feishu` 與完整 legacy 回歸保護；多 profile 期間產生的 `tenant_sessions.db`、scoped 記憶與事件在 legacy 模式不可見但不受損。
- **程式回滾：** `git revert $(cat .git/plan3-base-sha)..HEAD`。本計畫所有既有檔修改皆為向後相容的新增（新參數均有 legacy 預設值），revert 後舊版可直接運行；無 SQLite schema 變更、無資料遷移需要反向操作。
- **驗證回滾：** revert 後執行 `pytest -q` 與 legacy smoke test（普通聊天＋群告警各一則），確認單 App WS、輪詢與回覆正常。
- **禁止事項：** 回滾不得刪除 `runtime/tenant_sessions.db` 或任何既有 SQLite（規格 §20.3）；不得為了清除狀態而殺掉其他 App 的監督執行緒以外的程序。

## 驗收對照（規格 §22）

| §22 項目 | 本計畫覆蓋 | 驗證位置 |
|----------|-----------|----------|
| 1. 完整 pytest 零失敗 | ✅ | 任務 9 步驟 5 |
| 2. Python 編譯檢查 | ✅ | 任務 9 步驟 6 |
| 3. Legacy 普通聊天＋群告警 smoke | ✅（測試層） | 任務 7 啟動測試＋任務 9 步驟 4；真機 smoke 在計畫 5 複驗 |
| 4. 雙 AWS profile STS 端到端 | ⬜ | 計畫 4（STS 驗證）／計畫 5（端到端） |
| 5. 同 profile 多群隔離 | ✅（Session／記憶 key 層） | 計畫 2 已驗；本計畫 `test_same_chat_id_across_apps_does_not_collide` 補 App 維度 |
| 6. 並行任務正確 Account ID | ⬜ | 計畫 5（真實 STS） |
| 7. 未映射群拒絕／靜默且無子程序 | ✅ | `tests/test_multi_profile_message_pipeline.py` 四個 fail-closed 案例 |
| 8. 群告警原 App 回覆＋群綁定 AWS profile | ✅ | `test_alert_uses_route_profile_and_replies_via_original_app`、`test_alert_analysis_uses_context_aws_env_and_no_session` |
| 9. 無效熱載入保留 snapshot | ⬜（Registry 層計畫 1 已驗） | Dashboard 發布在計畫 4 |
| 10. 設定 revision 回滾 | ⬜ | 計畫 4 |
| 11. 應用版本回滾演練 | ⬜ | 計畫 5 |
| 12. 日誌無 Secret／credential | ✅（本計畫範圍） | `test_secrets_resolved_from_environ_names_and_never_logged`、任務 9 步驟 9 |

另對應規格 §21.2：多 Adapter 同時註冊且正確 App 回覆（`test_both_adapters_registered_under_own_app_key`、`test_reply_goes_through_originating_app`）、不同 App 相同 `chat_id` 不覆蓋（`test_same_chat_id_across_apps_does_not_collide`）、單 App 中斷不影響其他 App（`test_single_app_crash_does_not_stop_other_app`）、群告警正確 AWS profile 與原 App 回覆（同上）。
