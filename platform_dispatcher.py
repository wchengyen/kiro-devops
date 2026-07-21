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
        effective = app_key or getattr(adapter, "app_key", DEFAULT_APP_KEY)
        if not isinstance(effective, str) or not effective:
            # 兼容未定義 app_key 的適配器（含測試中的 MagicMock）
            effective = DEFAULT_APP_KEY
        key = (adapter.platform, effective)
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
