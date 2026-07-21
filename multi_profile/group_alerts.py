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
