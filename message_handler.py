#!/usr/bin/env python3
"""平台无关的消息业务处理核心."""
import logging
import os
import re
import shutil
import subprocess
import threading

from adapters.base import IncomingMessage, OutgoingPayload
from adapters.feishu import extract_file_paths
from kiro_executor import KiroExecutor, has_decision_signal
from platform_dispatcher import PlatformDispatcher
from scheduler import Scheduler
from session_router import SessionRouter

try:
    from prompt_builder import build_prompt, has_episodic_hint
except ImportError:
    build_prompt = None
    has_episodic_hint = None

log = logging.getLogger("message-handler")
kiro_bin = shutil.which("kiro-cli") or "/home/ubuntu/.local/bin/kiro-cli"
KIRO_TIMEOUT = int(os.environ.get("KIRO_TIMEOUT", "120"))
KIRO_AGENT = os.environ.get("KIRO_AGENT", "").strip()
ENABLE_MEMORY = os.environ.get("ENABLE_MEMORY", "false").lower() in ("true", "1", "yes")

if ENABLE_MEMORY:
    try:
        from memory import MemoryLayer
        from event_store import EventStore
        from event_ingest import parse_manual_command, ingest_to_store
    except ImportError as _e:
        log.warning(f"记忆依赖未安装: {_e}")
        ENABLE_MEMORY = False

memory = MemoryLayer() if ENABLE_MEMORY else None
event_store = EventStore() if ENABLE_MEMORY else None


class MessageHandler:
    def __init__(self, dispatcher: PlatformDispatcher):
        self.dispatcher = dispatcher
        self.session_router = SessionRouter(kiro_bin=kiro_bin, kiro_agent=KIRO_AGENT)
        self.kiro_executor = KiroExecutor(agent=KIRO_AGENT)
        self.scheduler = Scheduler(
            send_fn=self._send_to_target,
            kiro_fn=self._call_kiro_simple,
        )

    def _send_to_target(self, unified_user_id: str, text: str) -> None:
        """定时任务回调：根据 unified_id 路由到对应平台."""
        self.dispatcher.send(unified_user_id, text)
        # 检测并发送文本中的图片/文件
        images, files = extract_file_paths(text)
        for img in images:
            self.dispatcher.send_image(unified_user_id, img)
        for f in files:
            self.dispatcher.send_file(unified_user_id, f)

    def _call_kiro_simple(self, prompt: str) -> str:
        """简单调用（供定时任务使用）."""
        log.info(f"调用 kiro-cli (simple): {prompt[:80]}...")
        try:
            cmd = [kiro_bin, "chat", "--no-interactive", "-a", "--wrap", "never"]
            if KIRO_AGENT:
                cmd += ["--agent", KIRO_AGENT]
            bg_model = os.environ.get("BACKGROUND_MODEL", "").strip()
            if bg_model:
                cmd += ["--model", bg_model]
            cmd.append(prompt)
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=KIRO_TIMEOUT,
                cwd=os.path.expanduser("~"), env={**os.environ, "NO_COLOR": "1"},
            )
            output = result.stdout.strip() or result.stderr.strip() or "Kiro 未返回结果"
            return output
        except subprocess.TimeoutExpired:
            return f"⏰ Kiro 处理超时（{KIRO_TIMEOUT}s）"
        except Exception as e:
            return f"❌ Kiro 调用失败: {e}"

    def handle(self, incoming: IncomingMessage) -> None:
        """所有平台消息的统一入口."""
        user_id = incoming.unified_user_id
        text = incoming.text

        # 检测原始消息中是否包含图片/文件（即使 adapter 未下载也给出提示）
        raw_items = incoming.raw.get("item_list", []) if incoming.raw else []
        has_raw_image = any(i.get("type") == 2 for i in raw_items)
        has_raw_file = any(i.get("type") == 4 for i in raw_items)

        # 媒体接收已禁用，保持文字沟通
        if (incoming.images or has_raw_image) and not text:
            self._reply(incoming, "📷 收到图片，当前仅支持文字沟通。请发送文字消息。")
            return
        if (incoming.files or has_raw_file) and not text:
            self._reply(incoming, "📎 收到文件，当前仅支持文字沟通。请发送文字消息。")
            return
        if text and (incoming.images or incoming.files or has_raw_image or has_raw_file):
            # 忽略媒体，仅处理文字
            text = text.strip()

        if text.startswith("/schedule"):
            args = text[len("/schedule"):].strip()
            reply = self.scheduler.handle_command(user_id, args or "help", source_platform=incoming.platform)
            self._reply(incoming, reply)
            return

        if text.startswith("/memory"):
            if not ENABLE_MEMORY:
                self._reply(incoming, "🧠 记忆功能未启用。")
                return
            args = text[len("/memory"):].strip().lower()
            self._reply(incoming, self._handle_memory_command(user_id, args))
            return

        if text.startswith("/event"):
            if not ENABLE_MEMORY:
                self._reply(incoming, "🧠 记忆功能未启用。")
                return
            args = text[len("/event"):].strip()
            self._reply(incoming, self._handle_event_command(user_id, args))
            return

        if text.strip() == "/new":
            self.session_router.clear_active(user_id)
            self._reply(incoming, "🆕 已切换到新会话模式，下条消息将开启新对话。")
            return

        if text.strip().startswith("/resume"):
            parts = text.strip().split()
            if len(parts) < 2:
                self._reply(incoming, "用法：/resume <编号>\n发送 /sessions 查看可用会话。")
                return
            try:
                short_id = int(parts[1].lstrip("#"))
            except ValueError:
                self._reply(incoming, "❌ 请输入数字编号，如 /resume 1")
                return
            session = self.session_router.get_by_short_id(user_id, short_id)
            if not session:
                self._reply(incoming, f"❌ 未找到会话 #{short_id}，发送 /sessions 查看列表。")
                return
            self.session_router.touch(user_id, session["kiro_session_id"])
            self._reply(incoming, f"🔄 已恢复会话 #{short_id} {session['topic']}\n继续发消息即可。")
            return

        if text.strip() == "/sessions":
            self._reply(incoming, self.session_router.list_sessions(user_id))
            return

        if text.strip() == "/status":
            status = self.kiro_executor.get_status(user_id)
            self._reply(incoming, status or "没有正在运行的后台任务。")
            return

        if text.strip() == "/cancel":
            self._reply(incoming, self.kiro_executor.cancel(user_id))
            return

        if self.kiro_executor.is_busy(user_id):
            self._reply(incoming, "⏳ 上一个任务还在后台运行中，请等待完成或发送 /cancel 取消。")
            return

        self._reply(incoming, "🤖 正在处理，请稍候...")

        mem_enabled = ENABLE_MEMORY and memory and memory.is_enabled(user_id)
        if mem_enabled:
            memory.add(user_id, f"用户说：{text}")
        semantic_memories = memory.search(user_id, text) if mem_enabled else []
        episodic_memories = []
        if mem_enabled and event_store and has_episodic_hint and has_episodic_hint(text):
            raw_ents = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", text)
            raw_ents += re.findall(r"[\u4e00-\u9fff]{2,}", text)
            entities = [e for e in raw_ents if len(e) >= 2]
            episodic_memories = event_store.search_events(
                user_id, query=text, entities=entities or None, days=14, top_k=5
            )
            if episodic_memories:
                log.info(f"为用户 {user_id} 检索到 {len(episodic_memories)} 条相关事件")

        prompt = build_prompt(text, semantic_memories, episodic_memories) if build_prompt else text
        session_id = self.session_router.resolve(user_id, text)
        is_new = session_id is None

        def on_sync_result(output: str):
            self._deliver_result(incoming, output, session_id, is_new, mem_enabled, len(episodic_memories))

        def on_async_start():
            self._reply(incoming, "⏳ 任务较复杂，已转入后台处理。完成后会主动推送结果。\n发送 /status 查看进度，/cancel 取消。")

        def on_async_result(output: str):
            self._deliver_result(incoming, output, session_id, is_new, mem_enabled, len(episodic_memories))

        def on_progress(msg: str):
            self.dispatcher.send(user_id, msg)

        self.kiro_executor.execute(
            prompt, session_id, user_id,
            on_sync_result, on_async_start, on_async_result, on_progress
        )

    def _deliver_result(self, incoming: IncomingMessage, output: str, session_id, is_new, mem_enabled, episodic_count=0):
        if is_new:
            self.session_router.register_new(incoming.unified_user_id, incoming.text[:30])
            sessions = self.session_router._data.get(incoming.unified_user_id, [])
            sid = sessions[-1]["kiro_session_id"] if sessions else None
        else:
            sid = session_id
            self.session_router.touch(incoming.unified_user_id, session_id)

        suffix = ""
        if episodic_count > 0:
            suffix += f"\n\n📎 本次分析关联了 {episodic_count} 条历史事件（/memory events 查看全部）"
        if has_decision_signal(output):
            suffix += "\n\n💡 回复消息继续当前对话（自动延续上下文）"
        if sid:
            suffix += self.session_router.get_active_label(incoming.unified_user_id, sid)

        self._reply(incoming, output + suffix)

        if mem_enabled:
            conversation = f"用户：{incoming.text}\n助手：{output}"
            threading.Thread(target=memory.extract_and_store, args=(incoming.unified_user_id, conversation), daemon=True).start()

    def _reply(self, incoming: IncomingMessage, text: str) -> None:
        adapter = self.dispatcher.get_adapter(incoming.platform)
        if not adapter:
            log.error(f"找不到平台适配器: {incoming.platform}")
            return
        # 媒体发送已禁用，保持文字沟通
        payload = OutgoingPayload(text=text.strip())
        adapter.reply(incoming, payload)

    def _handle_memory_command(self, user_id: str, args: str) -> str:
        if args == "off":
            memory.set_enabled(user_id, False)
            return "🧠 记忆功能已关闭。\n发送 /memory on 可重新开启。"
        elif args == "on":
            memory.set_enabled(user_id, True)
            return "🧠 记忆功能已开启。"
        elif args == "clear":
            memory.clear(user_id)
            return "🗑️ 已清除你的所有记忆。"
        elif args == "status":
            enabled = memory.is_enabled(user_id)
            all_mem = memory.list_all(user_id)
            status = "开启 ✅" if enabled else "关闭 ❌"
            return f"🧠 记忆状态：{status}\n📊 语义记忆条数：{len(all_mem)}"
        elif args.startswith("events"):
            sub = args[len("events"):].strip()
            if sub == "clear":
                if event_store:
                    event_store.clear(user_id)
                return "🗑️ 已清除你的所有事件记录。"
            else:
                if not event_store:
                    return "📭 事件存储未启用。"
                events = event_store.list_events(user_id, days=30, limit=20)
                if not events:
                    return "📭 最近 30 天没有事件记录。"
                lines = ["📋 最近事件（最近 30 天）：\n"]
                for i, e in enumerate(events, 1):
                    ts = e.get("ts", "")[:10] if e.get("ts") else ""
                    lines.append(f"  {i}. [{e['event_type']}] {ts} {e['title']}")
                lines.append(f"\n共 {len(events)} 条，发送 /memory events clear 可清空")
                return "\n".join(lines)
        else:
            return (
                "🧠 记忆管理命令：\n"
                "/memory status - 查看记忆状态\n"
                "/memory on     - 开启记忆\n"
                "/memory off    - 关闭记忆\n"
                "/memory clear  - 清除所有语义记忆\n"
                "/memory events - 查看最近事件\n"
                "/memory events clear - 清空事件记录"
            )

    def _handle_event_command(self, user_id: str, args: str) -> str:
        if not args.strip():
            return (
                "📝 事件录入命令：\n"
                "/event 类型=系统变更 实体=test1,MySQL 标题=索引优化 描述=增加联合索引\n"
                "\n支持字段：类型、实体（逗号分隔）、标题、描述、级别、来源"
            )
        record = parse_manual_command(args)
        record["user_id"] = user_id
        if not record.get("title"):
            return "❌ 标题不能为空，请提供 标题=..."
        result = ingest_to_store(event_store, record)
        if result["ok"]:
            return f"✅ 已记录事件 #{result['event_id'][:8]}：{record['title']}\n关联实体：{', '.join(record.get('entities', []))}"
        else:
            return f"❌ 录入失败：{result['error']}"
