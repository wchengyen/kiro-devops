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
    ConfigPublisher,
    ConfigRegistry,
    ContextRuntime,
    GroupAlertRunner,
    MultiProfilePipeline,
    ProfileHealthMonitor,
    RevisionStore,
    SessionCaptureCoordinator,
    SessionStore,
    TaskRegistry,
    config_path,
    is_enabled,
    load_operational_settings,
    revision_dir_from_env,
)

from dashboard.multi_profile_api import MultiProfileDeps, init_multi_profile_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("gateway")

APP_ID = os.environ.get("FEISHU_APP_ID", "").strip()
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "").strip()
WEIXIN_BOT_TOKEN = os.environ.get("WEIXIN_BOT_TOKEN", "").strip() or None
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
TENANT_SESSION_DB = os.path.join(PROJECT_DIR, "runtime", "tenant_sessions.db")

# multi-profile 模式的健康監控（供關閉路徑 stop；legacy 模式為 None）
_HEALTH_MONITOR = None


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
    global _HEALTH_MONITOR
    registry = ConfigRegistry(config_path(project_dir=PROJECT_DIR))
    registry.load_initial()

    # 計畫 4：健康監控（值越界時啟動即失敗，帶明確錯誤）
    settings = load_operational_settings()
    health_monitor = ProfileHealthMonitor(registry.snapshot, settings=settings)
    health_monitor.check_all_now()   # 啟動時先檢查一輪，避免樂觀窗口過長
    health_monitor.start()           # daemon 執行緒；interval + jitter
    _HEALTH_MONITOR = health_monitor

    task_registry = TaskRegistry()
    session_store = SessionStore(TENANT_SESSION_DB)
    runtime = ContextRuntime(
        kiro_bin=os.environ.get("KIRO_BIN", "").strip() or "kiro-cli",
        session_store=session_store,
        session_capture=SessionCaptureCoordinator(),
        task_registry=task_registry,
    )
    alert_runner = GroupAlertRunner(dispatcher=dispatcher)
    pipeline = MultiProfilePipeline(
        registry=registry,
        dispatcher=dispatcher,
        runtime=runtime,
        session_store=session_store,
        alert_runner=alert_runner,
        parse_alert=None,  # 由下方 MessageHandler 建立後補上，避免循環依賴
        health_monitor=health_monitor,
    )
    handler = MessageHandler(dispatcher=dispatcher, mp_pipeline=pipeline)
    pipeline._parse_alert = handler._parse_structured_alert  # 沿用既有告警解析
    app_manager = AppManager(
        registry=registry,
        adapter_factory=lambda **kw: FeishuAdapter(**kw),
        dispatcher=dispatcher,
        on_message=handler.handle,
    )

    # 計畫 4：revision 儲存、原子發布器與 Dashboard 依賴注入
    revision_store = RevisionStore(
        revision_dir_from_env(os.environ, project_dir=PROJECT_DIR),
    )
    publisher = ConfigPublisher(
        registry=registry,
        revision_store=revision_store,
        health_monitor=health_monitor,
    )
    init_multi_profile_api(MultiProfileDeps(
        mode="multi-profile",
        config_path=registry.path,
        revision_dir=revision_store.directory,
        registry=registry,
        publisher=publisher,
        revision_store=revision_store,
        health_monitor=health_monitor,
        app_manager=app_manager,
        task_registry=task_registry,
        settings=settings,
    ))
    return handler, app_manager


def _init_offline_multi_profile_api():
    """legacy 模式注入離線 Dashboard 依賴（規格 §19.3）：

    可驗證與 bootstrap Draft，但不建立 health monitor、不影響 legacy runtime。
    """
    init_multi_profile_api(MultiProfileDeps(
        mode="legacy",
        config_path=config_path(os.environ, project_dir=PROJECT_DIR),
        revision_dir=revision_dir_from_env(os.environ, project_dir=PROJECT_DIR),
        settings=load_operational_settings(),
    ))


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
    _init_offline_multi_profile_api()  # 離線 Draft 驗證／bootstrap；不啟動任何 runtime
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
        if _HEALTH_MONITOR is not None:
            _HEALTH_MONITOR.stop()
        sys.exit(0)


def main():
    build_gateway()


if __name__ == "__main__":
    main()
