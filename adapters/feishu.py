#!/usr/bin/env python3
"""飞书平台适配器 — 支持 WebSocket + 群历史消息轮询."""
import json
import logging
import os
import re
import threading
import time
import urllib.request
from typing import Callable

import lark_oapi as lark
from lark_oapi.api.im.v1 import *

from .base import PlatformAdapter, IncomingMessage, OutgoingPayload

log = logging.getLogger("adapter-feishu")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
FILE_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".txt", ".zip", ".mp4", ".opus"}

# 消息去重缓存（WS 与轮询共用）
_processed_message_ids: set[str] = set()
_MAX_MSG_ID_CACHE = 1000


def _split_text(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def extract_file_paths(text: str) -> tuple[list[str], list[str]]:
    images, files = [], []
    for match in re.findall(r'(/[\w./_-]+\.[\w]+)', text):
        if not os.path.isfile(match):
            continue
        ext = os.path.splitext(match)[1].lower()
        if ext in IMAGE_EXTS:
            images.append(match)
        elif ext in FILE_EXTS:
            files.append(match)
    return images, files


class FeishuAdapter(PlatformAdapter):
    @property
    def platform(self) -> str:
        return "feishu"

    def __init__(self, app_id: str, app_secret: str, on_message: Callable[[IncomingMessage], None]):
        self.app_id = app_id
        self.app_secret = app_secret
        self.on_message = on_message
        self.client = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .log_level(lark.LogLevel.INFO) \
            .build()

        # === 轮询配置 ===
        self._poll_chat_ids = [
            c.strip()
            for c in os.environ.get("FEISHU_POLL_CHAT_IDS", "").split(",")
            if c.strip()
        ]
        self._poll_interval = int(os.environ.get("FEISHU_POLL_INTERVAL_SEC", "10"))
        # 每个群的最后处理 create_time（毫秒），避免重复
        self._last_poll_time: dict[str, int] = {}
        self._running = False
        self._shutdown_event = threading.Event()
        self._bot_open_id: str | None = None

    def _fetch_bot_open_id(self) -> str | None:
        """获取机器人自身的 open_id，用于过滤轮询时的自身消息.

        使用原生 HTTP 请求绕过 lark-oapi RawRequest 的兼容性问题.
        """
        try:
            # 1. 获取 tenant_access_token
            token_req = urllib.request.Request(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                data=json.dumps({"app_id": self.app_id, "app_secret": self.app_secret}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(token_req, timeout=10) as resp:
                token_data = json.loads(resp.read().decode("utf-8"))
            tenant_token = token_data.get("tenant_access_token")
            if not tenant_token:
                log.warning(f"获取 tenant_access_token 失败: {token_data}")
                return None

            # 2. 获取 bot info
            bot_req = urllib.request.Request(
                "https://open.feishu.cn/open-apis/bot/v3/info",
                headers={"Authorization": f"Bearer {tenant_token}"},
            )
            with urllib.request.urlopen(bot_req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("bot", {}).get("open_id")
        except Exception:
            log.exception("获取 Bot open_id 失败")
        return None

    def start(self) -> None:
        self._running = True

        # 获取 Bot 自身 open_id（用于过滤自身消息）
        if self._poll_chat_ids:
            self._bot_open_id = self._fetch_bot_open_id()
            if self._bot_open_id:
                log.info(f"Bot open_id: {self._bot_open_id}")

        # 1) WebSocket 线程
        ws_thread = threading.Thread(target=self._ws_loop, name="feishu-ws", daemon=True)
        ws_thread.start()

        # 2) 群轮询线程
        if self._poll_chat_ids:
            poll_thread = threading.Thread(target=self._poll_loop, name="feishu-poll", daemon=True)
            poll_thread.start()
            log.info(f"🚀 飞书适配器启动（WS + 轮询 {len(self._poll_chat_ids)} 个群，间隔 {self._poll_interval}s）")
        else:
            log.info("🚀 飞书适配器启动（仅 WebSocket）")

        # 阻塞，保持 start() 不返回（与原有 cli.start() 阻塞行为一致）
        self._shutdown_event.wait()

    def _ws_loop(self) -> None:
        handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self._on_lark_message) \
            .build()
        cli = lark.ws.Client(self.app_id, self.app_secret, event_handler=handler, log_level=lark.LogLevel.INFO)
        try:
            cli.start()
        except Exception:
            log.exception("飞书 WS 异常退出")
        finally:
            self._shutdown_event.set()

    def _poll_loop(self) -> None:
        """定期拉取指定群的历史消息."""
        # 首次启动时，设定只拉取「启动前 30 秒」之后的消息，避免一次性淹没
        # API 的 start_time 单位为秒
        boot_time_sec = int(time.time()) - 30
        for cid in self._poll_chat_ids:
            self._last_poll_time[cid] = boot_time_sec

        while self._running:
            for chat_id in self._poll_chat_ids:
                if not self._running:
                    break
                try:
                    self._poll_single_chat(chat_id)
                except Exception:
                    log.exception(f"轮询群消息失败: {chat_id}")
            # 小步睡眠，以便快速响应 shutdown
            for _ in range(self._poll_interval):
                if not self._running:
                    break
                time.sleep(1)

    def _poll_single_chat(self, chat_id: str) -> None:
        """拉取单个群的新消息并分发."""
        # 回溯 5 秒，防止边界漏消息；依赖 message_id 去重
        # start_time 单位为秒
        start_time = max(0, self._last_poll_time[chat_id] - 5)

        req = ListMessageRequest.builder() \
            .container_id_type("chat") \
            .container_id(chat_id) \
            .sort_type("ByCreateTimeAsc") \
            .page_size(50) \
            .start_time(str(start_time)) \
            .build()

        resp = self.client.im.v1.message.list(req)
        if not resp.success():
            log.warning(f"ListMessage 失败 {chat_id}: {resp.code} {resp.msg}")
            return

        items = resp.data.items or []
        if not items:
            return

        new_messages = []
        max_time_sec = self._last_poll_time[chat_id]

        for msg in items:
            # create_time 是毫秒时间戳，转为秒进行比较与存储
            create_time_sec = (int(msg.create_time) // 1000) if msg.create_time else 0

            # 更新水位线（即使是自身消息也要更新，防止下次重复拉取）
            if create_time_sec > max_time_sec:
                max_time_sec = create_time_sec

            # 过滤自身消息（避免循环）
            if self._bot_open_id and msg.sender and msg.sender.id == self._bot_open_id:
                continue

            # 全局去重（WS 与轮询共用）
            if msg.message_id in _processed_message_ids:
                continue
            _processed_message_ids.add(msg.message_id)
            if len(_processed_message_ids) > _MAX_MSG_ID_CACHE:
                half = list(_processed_message_ids)[_MAX_MSG_ID_CACHE // 2:]
                _processed_message_ids.clear()
                _processed_message_ids.update(half)

            # 只处理文本消息
            if msg.msg_type != "text":
                continue

            new_messages.append(msg)

        self._last_poll_time[chat_id] = max_time_sec

        # 按时间顺序分发
        for msg in new_messages:
            self._dispatch_polled_message(msg)

    def _dispatch_polled_message(self, msg) -> None:
        """将 ListMessage 返回的 Message 转成 IncomingMessage."""
        try:
            content = json.loads(msg.body.content or "{}")
            user_text = content.get("text", "").strip()
        except (json.JSONDecodeError, AttributeError):
            user_text = ""

        # 去除 @Bot 的标记
        mentions = msg.mentions or []
        for m in mentions:
            if m.key:
                user_text = user_text.replace(m.key, "").strip()

        if not user_text:
            return

        sender_id = (msg.sender.id or "unknown") if msg.sender else "unknown"
        # 判断是否有 @（mentions 中包含用户即为 @）
        is_at = any(
            (m.id_type or "").lower() in ("open_id", "user_id", "union_id")
            for m in mentions
        )

        incoming = IncomingMessage(
            platform="feishu",
            raw_user_id=sender_id,
            unified_user_id=f"feishu:{sender_id}",
            message_id=msg.message_id,
            text=user_text,
            chat_type="group",
            is_at_me=is_at,
            raw={"message": msg, "source": "poll"},
            group_id=msg.chat_id,
            group_name=msg.chat_id,
            sender_name=sender_id,
        )
        self.on_message(incoming)

    def _on_lark_message(self, data) -> None:
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        data: P2ImMessageReceiveV1
        message = data.event.message
        message_id = message.message_id
        msg_type = message.message_type
        chat_type = getattr(message, "chat_type", "unknown")
        log.info(f"[DEBUG] 收到飞书消息 msg_id={message_id} type={msg_type} chat={chat_type}")

        # 消息去重：飞书 WS 重连时可能重推历史事件
        if message_id in _processed_message_ids:
            log.info(f"忽略重复消息: {message_id}")
            return
        _processed_message_ids.add(message_id)
        if len(_processed_message_ids) > _MAX_MSG_ID_CACHE:
            # 防止内存无限增长，保留最近一半
            half = list(_processed_message_ids)[_MAX_MSG_ID_CACHE // 2:]
            _processed_message_ids.clear()
            _processed_message_ids.update(half)

        msg_type = message.message_type

        if msg_type != "text":
            self.reply(
                IncomingMessage(
                    platform="feishu", raw_user_id="", unified_user_id="",
                    message_id=message_id, text="", raw={}
                ),
                OutgoingPayload(text="目前只支持文本消息哦 📝")
            )
            return

        try:
            content = json.loads(message.content or "{}")
            user_text = content.get("text", "").strip()
        except json.JSONDecodeError:
            user_text = ""

        if data.event.message.mentions:
            for m in data.event.message.mentions:
                if m.key:
                    user_text = user_text.replace(m.key, "").strip()

        if not user_text:
            return

        user_id = data.event.sender.sender_id.open_id or "unknown"
        is_group = message.chat_type == "group"
        is_at = bool(data.event.message.mentions)

        # 群聊中未 @ 机器人则忽略
        # 例外：当开启群告警监听时，消息会传给 MessageHandler 做结构化检测，
        #       由 MessageHandler 根据 is_at_me 决定是否进入普通对话
        _GROUP_ALERT_LISTEN_ENABLED = os.environ.get("GROUP_ALERT_LISTEN_ENABLED", "false").lower() in ("true", "1", "yes")
        if is_group and not is_at and not _GROUP_ALERT_LISTEN_ENABLED:
            return

        group_id = getattr(message, "chat_id", None) or ""
        sender_name = getattr(data.event.sender, "name", None) or user_id

        incoming = IncomingMessage(
            platform="feishu",
            raw_user_id=user_id,
            unified_user_id=f"feishu:{user_id}",
            message_id=message_id,
            text=user_text,
            chat_type="group" if is_group else "private",
            is_at_me=is_at,
            raw={"message": message, "data": data},
            group_id=group_id or None,
            group_name=group_id or None,
            sender_name=sender_name or None,
        )
        self.on_message(incoming)

    def send_text(self, raw_user_id: str, text: str, context_token: str | None = None) -> None:
        chunks = _split_text(text, 4000)
        for chunk in chunks:
            req = CreateMessageRequest.builder() \
                .receive_id_type("open_id") \
                .request_body(CreateMessageRequestBody.builder()
                              .receive_id(raw_user_id)
                              .msg_type("text")
                              .content(json.dumps({"text": chunk}))
                              .build()) \
                .build()
            resp = self.client.im.v1.message.create(req)
            if not resp.success():
                log.error(f"主动发送失败: {resp.code} {resp.msg}")
                break
        log.info(f"已主动发送消息给 {raw_user_id}（{len(chunks)} 段）")

    def send_image(self, raw_user_id: str, image_path: str, context_token: str | None = None) -> bool:
        key = self.upload_image(image_path)
        if not key:
            return False
        req = CreateMessageRequest.builder() \
            .receive_id_type("open_id") \
            .request_body(CreateMessageRequestBody.builder()
                          .receive_id(raw_user_id)
                          .msg_type("image")
                          .content(json.dumps({"image_key": key}))
                          .build()) \
            .build()
        resp = self.client.im.v1.message.create(req)
        if not resp.success():
            log.error(f"主动发送图片失败: {resp.code} {resp.msg}")
            return False
        log.info(f"已主动发送图片给 {raw_user_id}")
        return True

    def send_file(self, raw_user_id: str, file_path: str, context_token: str | None = None) -> bool:
        key = self.upload_file(file_path)
        if not key:
            return False
        req = CreateMessageRequest.builder() \
            .receive_id_type("open_id") \
            .request_body(CreateMessageRequestBody.builder()
                          .receive_id(raw_user_id)
                          .msg_type("file")
                          .content(json.dumps({"file_key": key}))
                          .build()) \
            .build()
        resp = self.client.im.v1.message.create(req)
        if not resp.success():
            log.error(f"主动发送文件失败: {resp.code} {resp.msg}")
            return False
        log.info(f"已主动发送文件给 {raw_user_id}")
        return True

    def reply(self, incoming: IncomingMessage, payload: OutgoingPayload) -> None:
        message_id = incoming.message_id
        text = payload.text
        chunks = _split_text(text, 4000)
        for chunk in chunks:
            req = ReplyMessageRequest.builder() \
                .message_id(message_id) \
                .request_body(ReplyMessageRequestBody.builder()
                              .msg_type("text")
                              .content(json.dumps({"text": chunk}))
                              .build()) \
                .build()
            resp = self.client.im.v1.message.reply(req)
            if not resp.success():
                log.error(f"回复失败: {resp.code} {resp.msg}")
                break
        log.info(f"已回复消息 {message_id}（{len(chunks)} 段）")

        for img_path in payload.images:
            key = self.upload_image(img_path)
            if key:
                self._reply_image(message_id, key)
        for file_path in payload.files:
            key = self.upload_file(file_path)
            if key:
                self._reply_file(message_id, key)

    def _reply_image(self, message_id: str, image_key: str) -> None:
        req = ReplyMessageRequest.builder().message_id(message_id).request_body(
            ReplyMessageRequestBody.builder().msg_type("image").content(json.dumps({"image_key": image_key})).build()
        ).build()
        resp = self.client.im.v1.message.reply(req)
        if not resp.success():
            log.error(f"回复图片失败: {resp.code} {resp.msg}")

    def _reply_file(self, message_id: str, file_key: str) -> None:
        req = ReplyMessageRequest.builder().message_id(message_id).request_body(
            ReplyMessageRequestBody.builder().msg_type("file").content(json.dumps({"file_key": file_key})).build()
        ).build()
        resp = self.client.im.v1.message.reply(req)
        if not resp.success():
            log.error(f"回复文件失败: {resp.code} {resp.msg}")

    def upload_image(self, path: str) -> str | None:
        with open(path, "rb") as f:
            req = CreateImageRequest.builder().request_body(
                CreateImageRequestBody.builder().image_type("message").image(f).build()
            ).build()
            resp = self.client.im.v1.image.create(req)
        if resp.success():
            log.info(f"图片上传成功: {resp.data.image_key}")
            return resp.data.image_key
        log.error(f"图片上传失败: {resp.code} {resp.msg}")
        return None

    def upload_file(self, path: str) -> str | None:
        ext = os.path.splitext(path)[1].lower()
        type_map = {".opus": "opus", ".mp4": "mp4", ".pdf": "pdf", ".doc": "doc", ".docx": "doc",
                    ".xls": "xls", ".xlsx": "xls", ".ppt": "ppt", ".pptx": "ppt"}
        file_type = type_map.get(ext, "stream")
        with open(path, "rb") as f:
            req = CreateFileRequest.builder().request_body(
                CreateFileRequestBody.builder().file_type(file_type).file_name(os.path.basename(path)).file(f).build()
            ).build()
            resp = self.client.im.v1.file.create(req)
        if resp.success():
            log.info(f"文件上传成功: {resp.data.file_key}")
            return resp.data.file_key
        log.error(f"文件上传失败: {resp.code} {resp.msg}")
        return None
