#!/usr/bin/env python3
"""微信 iLink Bot API 适配器."""
import base64
import json
import logging
import os
import struct
import time
import urllib.request
import urllib.error
from typing import Callable

import qrcode

from .base import PlatformAdapter, IncomingMessage, OutgoingPayload

log = logging.getLogger("adapter-weixin")
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
TOKEN_FILE = os.path.expanduser("~/.kiro/weixin_token.json")


def _random_uin() -> str:
    return base64.b64encode(str(struct.unpack(">I", os.urandom(4))[0]).encode()).decode()


def _headers(token: str | None = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_uin(),
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(url: str, headers: dict | None = None, timeout: int = 35) -> dict:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(path: str, base_url: str, token: str, body: dict, timeout: int = 40) -> dict:
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    data = json.dumps({**body, "base_info": {"channel_version": "1.0.0"}}, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, headers=_headers(token), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _split_text(text: str, limit: int = 2000) -> list[str]:
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


class WeixinAdapter(PlatformAdapter):
    platform = "weixin"

    def __init__(self, bot_token: str | None, on_message: Callable[[IncomingMessage], None]):
        self.bot_token = bot_token
        self.base_url = DEFAULT_BASE_URL
        self.on_message = on_message
        self._get_updates_buf = ""
        self._context_tokens: dict[str, str] = {}
        self._running = False
        self._load_token()

    def _load_token(self) -> None:
        if self.bot_token:
            return
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE) as f:
                data = json.load(f)
            self.bot_token = data.get("bot_token")
            self.base_url = data.get("base_url", DEFAULT_BASE_URL)
            log.info("已从本地文件加载微信 token")

    def _save_token(self) -> None:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            json.dump({"bot_token": self.bot_token, "base_url": self.base_url}, f)

    def _qr_login(self) -> None:
        log.info("=== 微信扫码登录 ===")
        base = self.base_url.rstrip("/") + "/"
        qr_resp = _get(base + "ilink/bot/get_bot_qrcode?bot_type=3")
        qrcode_id = qr_resp.get("qrcode")
        qrcode_url = qr_resp.get("qrcode_img_content")
        print(f"\n📱 请用微信扫描下方二维码登录 Bot:\n", flush=True)
        try:
            qr = qrcode.QRCode(border=1)
            qr.add_data(qrcode_url)
            qr.print_ascii(invert=True)
        except Exception as e:
            log.warning(f"打印二维码失败: {e}")
        print(f"\n或复制链接到浏览器: {qrcode_url}\n", flush=True)

        poll_url = base + f"ilink/bot/get_qrcode_status?qrcode={qrcode_id}"
        deadline = time.time() + 480
        headers = {"iLink-App-ClientVersion": "1"}

        while time.time() < deadline:
            try:
                status = _get(poll_url, headers)
            except Exception as e:
                log.warning(f"轮询错误: {e}")
                time.sleep(2)
                continue

            st = status.get("status", "wait")
            if st == "wait":
                print(".", end="", flush=True)
            elif st == "scaned":
                print("\n👀 已扫码，请在微信中点击确认...", flush=True)
            elif st == "confirmed":
                self.bot_token = status.get("bot_token")
                self.base_url = status.get("baseurl", DEFAULT_BASE_URL)
                self._save_token()
                print(f"\n✅ 微信登录成功！", flush=True)
                return
            elif st == "expired":
                raise RuntimeError("二维码已过期，请重新运行程序。")
            time.sleep(1)
        raise RuntimeError("登录超时（8分钟），请重试。")

    def start(self) -> None:
        if not self.bot_token:
            self._qr_login()
        self._running = True
        log.info("🚀 微信适配器启动（iLink 长轮询）")
        self._poll_loop()

    def _poll_loop(self) -> None:
        consecutive_errors = 0
        while self._running:
            try:
                resp = _post(
                    "ilink/bot/getupdates",
                    self.base_url,
                    self.bot_token,
                    {"get_updates_buf": self._get_updates_buf}
                )
                consecutive_errors = 0

                if resp.get("ret") != 0:
                    err = resp.get("errcode")
                    if err == -14:
                        log.warning("微信 session 过期，重新登录...")
                        self._qr_login()
                        continue
                    log.warning(f"getupdates 返回错误: {resp}")
                    time.sleep(5)
                    continue

                self._get_updates_buf = resp.get("get_updates_buf", self._get_updates_buf)
                msgs = resp.get("msgs") or []
                for msg in msgs:
                    self._handle_incoming(msg)

            except urllib.error.HTTPError as e:
                consecutive_errors += 1
                log.warning(f"HTTP 错误 ({consecutive_errors}/3): {e.code}")
                if consecutive_errors >= 3:
                    log.error("连续 3 次错误，暂停 30 秒后重试")
                    time.sleep(30)
                    consecutive_errors = 0
                else:
                    time.sleep(5)
            except Exception as e:
                log.exception("微信轮询异常")
                time.sleep(10)

    def _handle_incoming(self, msg: dict) -> None:
        if msg.get("message_type") != 1:  # 只处理用户消息
            return
        from_user = msg.get("from_user_id", "")
        context_token = msg.get("context_token", "")
        if context_token:
            self._context_tokens[from_user] = context_token

        text = ""
        items = msg.get("item_list") or []
        for item in items:
            if item.get("type") == 1:
                text = item.get("text_item", {}).get("text", "")
                break

        if not text:
            return

        incoming = IncomingMessage(
            platform="weixin",
            raw_user_id=from_user,
            unified_user_id=f"weixin:{from_user}",
            message_id=msg.get("client_id", "") or str(time.time()),
            text=text.strip(),
            chat_type="private",
            is_at_me=False,
            context_token=context_token,
            raw=msg,
        )
        self.on_message(incoming)

    def send_text(self, raw_user_id: str, text: str, context_token: str | None = None) -> None:
        ctx = context_token or self._context_tokens.get(raw_user_id)
        if not ctx:
            log.error(f"无法主动推送给 {raw_user_id}：缺少 context_token")
            return
        chunks = _split_text(text, 2000)
        for chunk in chunks:
            body = {
                "msg": {
                    "to_user_id": raw_user_id,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": ctx,
                    "item_list": [{"type": 1, "text_item": {"text": chunk}}],
                }
            }
            try:
                resp = _post("ilink/bot/sendmessage", self.base_url, self.bot_token, body)
                if resp.get("ret") != 0:
                    log.error(f"微信发送失败: {resp}")
            except Exception as e:
                log.error(f"微信发送异常: {e}")

    def reply(self, incoming: IncomingMessage, payload: OutgoingPayload) -> None:
        self.send_text(incoming.raw_user_id, payload.text, incoming.context_token)

    def upload_image(self, path: str) -> str | None:
        log.warning("微信图片上传一期未实现")
        return None

    def upload_file(self, path: str) -> str | None:
        log.warning("微信文件上传一期未实现")
        return None
