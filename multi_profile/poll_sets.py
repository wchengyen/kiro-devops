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
