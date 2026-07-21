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
            self._parse_alert(text)
            if incoming.chat_type == "group" and self._parse_alert is not None
            else None
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
                if "profile is unavailable" in str(error):
                    self._reply(incoming, _PROFILE_UNAVAILABLE_REPLY)
                else:
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
        if text == "/sessions":
            self._reply(incoming, self._format_sessions(context))
            return
        if text.startswith("/resume"):
            self._handle_resume(context, incoming, text)
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

    # ---- Session 清單／恢復（計畫 2 的 SessionStore 介面） ----

    def _format_sessions(self, context: ExecutionContext) -> str:
        sessions = self._session_store.list_sessions(context)
        if not sessions:
            return "📂 当前没有可恢复的会话。"
        lines = ["📂 可恢复的会话："]
        for s in sessions:
            lines.append(f"#{s.short_id} {s.topic}（消息 {s.message_count} 条）")
        lines.append("发送 /resume <编号> 恢复会话。")
        return "\n".join(lines)

    def _handle_resume(
        self, context: ExecutionContext, incoming: IncomingMessage, text: str
    ) -> None:
        parts = text.split()
        if len(parts) < 2:
            self._reply(incoming, self._format_sessions(context))
            return
        try:
            short_id = int(parts[1].lstrip("#"))
        except ValueError:
            self._reply(incoming, "❌ 请输入数字编号，如 /resume 1")
            return
        # fingerprint 不符時 SessionStore 依計畫 2 行為回傳 None（拒絕跨 profile 恢復）
        session = self._session_store.get_by_short_id(context, short_id)
        if session is None:
            self._reply(incoming, f"❌ 未找到会话 #{short_id}，发送 /sessions 查看列表。")
            return
        self._session_store.touch(context, session.kiro_session_id)
        self._reply(incoming, f"🔄 已恢复会话 #{short_id} {session.topic}\n继续发消息即可。")

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
