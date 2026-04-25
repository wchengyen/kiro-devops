# 微信渠道接入设计文档

**日期**: 2026-04-25  
**主题**: 通过 iLink Bot API 将微信接入 kiro-devops，建立第二个联通渠道  
**方案**: PlatformAdapter 抽象层（方案 A，单进程多线程）  
**约束**: 本地 git 改造，不推 GitHub

---

## 1. 背景与目标

### 1.1 现状

kiro-devops 当前仅支持飞书（Lark）单一沟通渠道，`app.py` 666 行代码深度耦合飞书 SDK（`lark-oapi`）的消息收发、文件上传、WebSocket 长连接等逻辑。

### 1.2 目标

- 同时运行 **飞书** 和 **微信** 两个沟通渠道
- 微信使用腾讯官方 iLink Bot API（`ilinkai.weixin.qq.com`），扫码登录 + HTTP 长轮询收消息
- 所有 `/` 命令在两个平台行为一致
- 告警可推送到多个平台，定时任务原路返回

### 1.3 非目标

- 跨平台用户身份合并（取消 `/bind` 配对码机制）
- 微信群聊支持（一期不明确）
- 微信图片/文件收发（一期仅文本）
- 微信语音消息
- Dashboard 中可视化配置平台偏好

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      kiro-devops gateway                        │
│                     （单进程，多线程并发）                         │
├──────────────────┬──────────────────┬───────────────────────────┤
│  FeishuAdapter   │  WeixinAdapter   │    WebhookAdapter         │
│  (lark-oapi WS)  │ (iLink 长轮询)    │    (Flask HTTP)           │
└────────┬─────────┘└────────┬─────────┘└───────────┬───────────┘
         │                   │                      │
         └───────────────────┼──────────────────────┘
                             ▼
              ┌────────────────────────────┐
              │      MessageNormalizer       │
              │  统一为 IncomingMessage      │
              └─────────────┬──────────────┘
                            ▼
              ┌────────────────────────────┐
              │      MessageHandler          │
              │  /schedule /memory /new      │
              │  → KiroExecutor → 生成回复    │
              └─────────────┬──────────────┘
                            ▼
              ┌────────────────────────────┐
              │     PlatformDispatcher       │
              │  根据 platform:raw_id 路由   │
              └────────────────────────────┘
```

### 2.1 核心设计原则

- **平台无关的业务核心**：命令解析、Kiro CLI 调用、记忆检索、会话路由全部与平台解耦
- `platform:raw_user_id` 作为内部统一用户标识（如 `feishu:ou_xxx`、`weixin:wxid_xxx@im.wechat`）
- 各平台用户数据天然隔离，不做跨平台身份合并

---

## 3. 用户模型

### 3.1 标识策略

内部统一用户标识直接使用 `platform:raw_user_id`：

```python
unified_id = f"{platform}:{raw_user_id}"
# 示例: "feishu:ou_xxxxxxxxxxxxxxxx"
#       "weixin:o9cq800kum_xxx@im.wechat"
```

- `memory.py`、`session_router.py`、`event_store.py` 全部直接使用 unified_id
- 飞书用户和微信用户的数据天然隔离
- 同一个自然人使用两个平台 = 系统视为两个独立用户（有意简化）

### 3.2 告警推送目标配置

告警接收方从单一 `ALERT_NOTIFY_USER_ID` 扩展为目标列表：

```bash
# .env
ALERT_NOTIFY_TARGETS=feishu:ou_xxxxxxxx,weixin:wxid_xxxxxxxx@im.wechat
```

- 解析为 `["feishu:ou_xxx", "weixin:wxid_xxx"]`
- 告警分析完成后，遍历列表，对每个目标调用 `PlatformDispatcher.send()`
- 向后兼容：代码启动时先读 `ALERT_NOTIFY_TARGETS`，若为空则回退读取 `ALERT_NOTIFY_USER_ID`，自动前缀为 `feishu:`，保证旧配置零改动可用

### 3.3 定时任务来源记录

`scheduled_jobs.json` 中新增字段：

```json
{
  "job_id": "abc123",
  "prompt": "每天上午9点检查 AWS 费用",
  "cron": "0 9 * * *",
  "created_by": "feishu:ou_xxxxxxxx",
  "notify_target": "feishu:ou_xxxxxxxx",
  "enabled": true
}
```

- `created_by` 和 `notify_target` 都记录为 `platform:raw_id`
- 到点执行后，通过 `PlatformDispatcher.send(notify_target, result)` 推送
- 即：在飞书创建的任务 → 结果推送到飞书；在微信创建的任务 → 结果推送到微信

---

## 4. 平台适配器接口

### 4.1 抽象基类

```python
# adapters/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class IncomingMessage:
    platform: str
    raw_user_id: str
    unified_user_id: str      # platform:raw_user_id
    message_id: str
    text: str
    chat_type: str            # "private" | "group"
    is_at_me: bool
    context_token: str | None = None
    raw: dict = field(default_factory=dict)

@dataclass
class OutgoingPayload:
    text: str
    images: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)

class PlatformAdapter(ABC):
    @property
    @abstractmethod
    def platform(self) -> str: ...

    @abstractmethod
    def start(self) -> None:
        """启动监听（阻塞或后台线程）"""

    @abstractmethod
    def send_text(self, raw_user_id: str, text: str, context_token: str | None = None) -> None:
        """主动推送文本"""

    @abstractmethod
    def reply(self, incoming: IncomingMessage, payload: OutgoingPayload) -> None:
        """回复某条 incoming 消息"""

    @abstractmethod
    def upload_image(self, path: str) -> str | None:
        """上传图片，返回平台特定的 media_key"""

    @abstractmethod
    def upload_file(self, path: str) -> str | None:
        """上传文件，返回平台特定的 file_key"""
```

### 4.2 飞书适配器（迁移现有逻辑）

- 将 `app.py` 中的飞书客户端初始化、WebSocket 事件监听、`send_message()`、`reply_message()`、`upload_image()`、`upload_file()` 迁移到 `adapters/feishu.py`
- 实现 `PlatformAdapter` 接口
- 群聊 `@` 处理逻辑保留在适配器内（去除 mention），不向业务层透传

### 4.3 微信适配器（新建）

**连接方式**：iLink HTTP 长轮询（`POST /ilink/bot/getupdates`，hold 35s）

**扫码登录流程**：

```
1. GET /ilink/bot/get_bot_qrcode?bot_type=3
   → 返回 qrcode（轮询ID）+ qrcode_img_content（二维码链接）
2. 终端打印二维码 URL
3. GET /ilink/bot/get_qrcode_status?qrcode=xxx（每秒轮询）
   状态机: wait → scaned → confirmed（或 expired）
4. confirmed 后返回 bot_token + baseurl，持久化到 ~/.kiro/weixin_token.json
```

**关键实现细节**：

| 特性 | 实现 |
|------|------|
| 鉴权头 | `AuthorizationType: ilink_bot_token` + `Authorization: Bearer <bot_token>` + `X-WECHAT-UIN: <random_uint32_base64>` |
| context_token 缓存 | `dict[raw_user_id, context_token]`，收到消息后缓存；主动推送和回复时必须携带 |
| 文本拆分 | 保守按 2000 字符分段（待实测确认上限）|
| 媒体收发 | 一期不做 |
| 会话过期 (-14) | 自动重新扫码（或重试 3 次后重新扫码）|

### 4.4 平台差异对照

| 特性 | 飞书 | 微信 iLink |
|------|------|-----------|
| 连接方式 | WebSocket（lark-oapi SDK）| HTTP 长轮询（35s hold）|
| 回复方式 | `message_id` reply | `context_token` 关联 |
| 主动推送 | 直接 `open_id` 发 | 需要缓存的 `context_token` |
| 媒体上传 | 飞书 IM API | CDN 预签名 + AES-128-ECB 加密（一期不做）|
| 群聊 @ | 需处理 `mentions` | 微信单聊为主，群聊待验证 |
| 文本分段 | 4000 字符 | 保守 2000 字符（一期）|

---

## 5. 消息处理流程

### 5.1 Gateway 启动（gateway.py）

```python
from adapters.feishu import FeishuAdapter
from adapters.weixin import WeixinAdapter
from message_handler import MessageHandler
from platform_dispatcher import PlatformDispatcher
from webhook_server import start_webhook_server

dispatcher = PlatformDispatcher()
handler = MessageHandler(dispatcher=dispatcher)

feishu = FeishuAdapter(
    app_id=os.getenv("FEISHU_APP_ID"),
    app_secret=os.getenv("FEISHU_APP_SECRET"),
    on_message=handler.handle,
)
weixin = WeixinAdapter(
    bot_token=os.getenv("WEIXIN_BOT_TOKEN") or None,
    on_message=handler.handle,
)

dispatcher.register(feishu)
dispatcher.register(weixin)

# 两个适配器各自在独立线程运行
threading.Thread(target=feishu.start, name="feishu-ws", daemon=True).start()
threading.Thread(target=weixin.start, name="weixin-poll", daemon=True).start()

start_webhook_server(handler)
```

### 5.2 MessageHandler（平台无关的业务核心）

```python
# message_handler.py
class MessageHandler:
    def __init__(self, dispatcher: PlatformDispatcher):
        self.dispatcher = dispatcher
        self.session_router = SessionRouter(...)
        self.kiro_executor = KiroExecutor(...)
        self.scheduler = Scheduler(
            send_fn=self._send_to_target,
            kiro_fn=call_kiro_simple,
        )

    def _send_to_target(self, unified_user_id: str, text: str):
        """定时任务回调：根据 unified_id 路由到对应平台"""
        self.dispatcher.send(unified_user_id, text)

    def handle(self, incoming: IncomingMessage):
        """所有平台消息的统一入口"""
        user_id = incoming.unified_user_id
        text = incoming.text

        # 命令处理（与平台无关）
        if text.startswith("/schedule"):
            reply = self.scheduler.handle_command(
                user_id, text, source_platform=incoming.platform
            )
            self._reply(incoming, reply)
            return
        if text.startswith("/memory"):
            ...
        if text.strip() == "/new":
            self.session_router.clear_active(user_id)
            self._reply(incoming, "🆕 已切换到新会话...")
            return
        # ... 其他命令

        # Kiro 执行
        self._reply(incoming, "🤖 正在处理...")
        # 记忆检索 → PromptBuilder → KiroExecutor
        # 回调中使用 self._reply(incoming, output)

    def _reply(self, incoming: IncomingMessage, text: str):
        adapter = self.dispatcher.get_adapter(incoming.platform)
        adapter.reply(incoming, OutgoingPayload(text=text))
```

### 5.3 PlatformDispatcher（发送路由）

```python
class PlatformDispatcher:
    def __init__(self):
        self._adapters: dict[str, PlatformAdapter] = {}

    def register(self, adapter: PlatformAdapter):
        self._adapters[adapter.platform] = adapter

    def send(self, unified_user_id: str, text: str):
        platform, raw_id = unified_user_id.split(":", 1)
        adapter = self._adapters.get(platform)
        if not adapter:
            log.error(f"未知平台: {platform}")
            return
        ctx = None
        if platform == "weixin":
            ctx = getattr(adapter, "_context_tokens", {}).get(raw_id)
        adapter.send_text(raw_id, text, context_token=ctx)

    def get_adapter(self, platform: str) -> PlatformAdapter | None:
        return self._adapters.get(platform)
```

---

## 6. 文件结构变更

```
kiro-devops/
├── gateway.py                 # 新：统一入口，启动所有适配器
├── message_handler.py         # 新：平台无关的业务核心（从 app.py 抽离）
├── platform_dispatcher.py     # 新：发送路由
├── adapters/
│   ├── __init__.py
│   ├── base.py                # 新：PlatformAdapter 抽象 + 数据类
│   ├── feishu.py              # 新：飞书适配器（从 app.py 迁移）
│   └── weixin.py              # 新：微信 iLink 适配器
├── webhook_server.py          # 新：Webhook HTTP 服务（从 app.py 迁移）
├── session_router.py          # 不改：接口兼容，传入 unified_id 字符串即可
├── memory.py                  # 不改：接口兼容
├── kiro_executor.py           # 不改
├── scheduler.py               # 改：增加 source_platform / notify_target 记录
├── prompt_builder.py          # 不改
├── event_store.py             # 不改
├── event_ingest.py            # 不改
├── app.py                     # 删：完全废弃，由 gateway.py 替代
├── start.sh                   # 改：python3 gateway.py
└── README.md                  # 改：新增多平台支持章节
```

---

## 7. 环境变量

```bash
# === 飞书（保持现有，可选填）===
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# === 微信 iLink（新增）===
WEIXIN_BOT_TOKEN=                      # 可选；留空则启动时扫码
# WEIXIN_TOKEN_FILE=~/.kiro/weixin_token.json

# === 告警推送（改造）===
# 旧：ALERT_NOTIFY_USER_ID=ou_xxx
# 新：支持多目标，逗号分隔
ALERT_NOTIFY_TARGETS=feishu:ou_xxxxxxxx,weixin:wxid_xxxxxxxx@im.wechat

# === 其他保持现有 ===
KIRO_TIMEOUT=120
WEBHOOK_ENABLED=true
WEBHOOK_PORT=8080
DASHBOARD_TOKEN=...
```

---

## 8. 错误处理与降级

| 场景 | 行为 |
|------|------|
| 飞书凭证缺失 | 不启动 FeishuAdapter，日志提示"飞书未配置，跳过"；系统仍可仅运行微信 |
| 微信扫码超时 | 终端打印二维码后等待 8 分钟，超时退出并提示重试 |
| 微信 session 过期（errcode -14） | WeixinAdapter 重试 3 次后自动重新扫码 |
| 微信 context_token 丢失 | 无法主动推送，但用户发新消息后会重新缓存，推送自动恢复 |
| 一个适配器崩溃 | 另一个适配器继续运行；崩溃线程退出，不影响整体进程 |
| 定时任务目标平台离线 | 记录失败日志，不重试，避免消息堆积 |

---

## 9. 测试策略

| 测试项 | 方式 |
|--------|------|
| FeishuAdapter 迁移后一致性 | 运行现有 `tests/test_dashboard_*.py` + 手动飞书发消息验证 |
| WeixinAdapter 扫码登录 | 手动测试：删除 token 文件，启动 gateway，扫码验证 |
| WeixinAdapter 文本收发 | 手动测试：微信发文本 → Bot 回复 → 验证 context_token 缓存 |
| 定时任务来源记录 | 在飞书创建任务 → 确认 `scheduled_jobs.json` 中 `notify_target` 正确 |
| 告警多目标推送 | 配置两个平台目标 → curl 模拟告警 → 两边都收到 |
| 单适配器故障隔离 | 启动双平台后模拟微信故障 → 飞书继续可用 |

---

## 10. README 新增内容

改造完成后，README 需新增「多平台支持」章节，包括：

- 飞书 vs 微信的能力对照表
- 微信扫码接入步骤
- 告警推送策略说明（单目标直接推，多目标全推）
- 定时任务原路返回说明
- 命令兼容性说明（微信暂不支持媒体上传）

---

## 11. 一期范围边界

| 功能 | 一期 | 二期 |
|------|------|------|
| 微信文本收发 | ✅ | — |
| 微信图片/文件收发 | ❌ | ✅（需 CDN AES-128-ECB 加密上传）|
| 微信群聊支持 | ❌ | 待验证 |
| 微信语音消息 | ❌ | ✅ |
| 跨平台用户绑定 | ❌（已取消）| — |
| Dashboard 平台选择配置 | ❌ | ✅ |
| 微信"对方正在输入" | ❌ | ✅ |

---

## 附录：iLink 核心 API 参考

| 接口 | 方法 | 用途 |
|------|------|------|
| `/ilink/bot/get_bot_qrcode` | GET | 获取登录二维码（`?bot_type=3`）|
| `/ilink/bot/get_qrcode_status` | GET | 轮询扫码状态 |
| `/ilink/bot/getupdates` | POST | 长轮询收消息（35s hold）|
| `/ilink/bot/sendmessage` | POST | 发送文本/媒体消息（必须带 `context_token`）|
| `/ilink/bot/getconfig` | POST | 获取 typing_ticket |
| `/ilink/bot/sendtyping` | POST | 显示/隐藏输入状态 |

---

*设计完成，等待实现计划。*
