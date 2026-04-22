#!/usr/bin/env python3
"""
飞书 Bot ↔ Kiro CLI 桥接服务（WebSocket 长连接版）
使用飞书 SDK 的 WebSocket 模式，无需公网 IP / 端口开放
"""
import os
import json
import time
import shutil
import logging
import subprocess
import threading

import lark_oapi as lark
from lark_oapi.api.im.v1 import *

import re
from scheduler import Scheduler
from session_router import SessionRouter
from kiro_executor import KiroExecutor, has_decision_signal

ENABLE_MEMORY = os.environ.get("ENABLE_MEMORY", "false").lower() in ("true", "1", "yes")
if ENABLE_MEMORY:
    try:
        from memory import MemoryLayer
    except ImportError:
        logging.warning("记忆依赖未安装（chromadb/sentence-transformers），已自动关闭记忆功能")
        ENABLE_MEMORY = False

# ============ 配置 ============
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
KIRO_TIMEOUT = int(os.environ.get("KIRO_TIMEOUT", "120"))
KIRO_AGENT = os.environ.get("KIRO_AGENT", "").strip()

# ============ 日志 ============
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("feishu-kiro")

# 去重
_processed = set()
_processed_lock = threading.Lock()

# ============ 记忆层 ============
memory = MemoryLayer() if ENABLE_MEMORY else None

# ============ 飞书客户端 ============
client = lark.Client.builder() \
    .app_id(APP_ID) \
    .app_secret(APP_SECRET) \
    .log_level(lark.LogLevel.INFO) \
    .build()


# ============ 飞书消息发送 ============
def send_message(user_id: str, text: str):
    """主动给用户发消息（用于定时任务）"""
    chunks = _split_text(text, 4000)
    for chunk in chunks:
        req = CreateMessageRequest.builder() \
            .receive_id_type("open_id") \
            .request_body(CreateMessageRequestBody.builder()
                          .receive_id(user_id)
                          .msg_type("text")
                          .content(json.dumps({"text": chunk}))
                          .build()) \
            .build()
        resp = client.im.v1.message.create(req)
        if not resp.success():
            log.error(f"主动发送失败: {resp.code} {resp.msg}")
            break
    log.info(f"已主动发送消息给 {user_id}（{len(chunks)} 段）")


def reply_message(message_id: str, text: str):
    chunks = _split_text(text, 4000)
    for chunk in chunks:
        req = ReplyMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(ReplyMessageRequestBody.builder()
                          .msg_type("text")
                          .content(json.dumps({"text": chunk}))
                          .build()) \
            .build()
        resp = client.im.v1.message.reply(req)
        if not resp.success():
            log.error(f"回复失败: {resp.code} {resp.msg}")
            break
    log.info(f"已回复消息 {message_id}（{len(chunks)} 段）")


def upload_image(path: str) -> str | None:
    """上传图片到飞书，返回 image_key"""
    with open(path, "rb") as f:
        req = CreateImageRequest.builder().request_body(
            CreateImageRequestBody.builder().image_type("message").image(f).build()
        ).build()
        resp = client.im.v1.image.create(req)
    if resp.success():
        log.info(f"图片上传成功: {resp.data.image_key}")
        return resp.data.image_key
    log.error(f"图片上传失败: {resp.code} {resp.msg}")
    return None


def upload_file(path: str) -> str | None:
    """上传文件到飞书，返回 file_key"""
    ext = os.path.splitext(path)[1].lower()
    type_map = {".opus": "opus", ".mp4": "mp4", ".pdf": "pdf", ".doc": "doc", ".docx": "doc",
                ".xls": "xls", ".xlsx": "xls", ".ppt": "ppt", ".pptx": "ppt"}
    file_type = type_map.get(ext, "stream")
    with open(path, "rb") as f:
        req = CreateFileRequest.builder().request_body(
            CreateFileRequestBody.builder().file_type(file_type).file_name(os.path.basename(path)).file(f).build()
        ).build()
        resp = client.im.v1.file.create(req)
    if resp.success():
        log.info(f"文件上传成功: {resp.data.file_key}")
        return resp.data.file_key
    log.error(f"文件上传失败: {resp.code} {resp.msg}")
    return None


def reply_image(message_id: str, image_key: str):
    """回复图片消息"""
    req = ReplyMessageRequest.builder().message_id(message_id).request_body(
        ReplyMessageRequestBody.builder().msg_type("image").content(json.dumps({"image_key": image_key})).build()
    ).build()
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        log.error(f"回复图片失败: {resp.code} {resp.msg}")


def reply_file(message_id: str, file_key: str):
    """回复文件消息"""
    req = ReplyMessageRequest.builder().message_id(message_id).request_body(
        ReplyMessageRequestBody.builder().msg_type("file").content(json.dumps({"file_key": file_key})).build()
    ).build()
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        log.error(f"回复文件失败: {resp.code} {resp.msg}")


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
FILE_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".txt", ".zip", ".mp4", ".opus"}


def extract_file_paths(text: str) -> tuple[list[str], list[str]]:
    """从文本中提取存在的图片和文件路径"""
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


def _split_text(text: str, limit: int = 4000) -> list[str]:
    """按换行符分段，每段不超过 limit 字符"""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # 在 limit 范围内找最后一个换行
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def strip_ansi(text: str) -> str:
    """去除 ANSI 转义码和终端控制字符"""
    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z?]', '', text)
    text = re.sub(r'\x1b\].*?\x07', '', text)
    # 去掉 kiro 的启动横幅（ASCII art logo + trust warning + credits）
    lines = text.split('\n')
    clean = []
    skip = False
    for line in lines:
        stripped = line.strip()
        if 'All tools are now trusted' in stripped or 'understand the risks' in stripped:
            continue
        if 'Learn more at' in stripped and 'kiro.dev' in stripped:
            continue
        if 'Credits:' in stripped and 'Time:' in stripped:
            continue
        if '/model' in stripped and 'to change' in stripped:
            continue
        if '/prompts' in stripped or 'Did you know' in stripped:
            continue
        # 跳过 ASCII art（连续的特殊 Unicode 块字符行）
        if stripped and all(c in '⠀⢀⣴⣶⣦⡀⣾⠁⠈⠙⣿⡆⢰⠋⢸⣇⡿⢻⣧⠹⣷⡄⠘⣆⠻⠿⠟⣠⡁⢹⣼⠇⠸⣄⢁⣤⠉⡇⠃⠂⠐⠒⠲⠶⠤⠖⠛⠏⠗⠞⠝⠜⠚⠘⠙⠑⠊⠉⠋⠌⠍⠎⠏⡏⡇⡆⡅⡄⡃⡂⡁⡀⢿⣿⣽⣻⣺⣹⣸⣷⣵⣳⣲⣱⣰⣯⣮⣭⣬⣫⣪⣩⣨⣧⣥⣤⣣⣢⣡⣠⣟⣞⣝⣜⣛⣚⣙⣘⣗⣖⣕⣔⣓⣒⣑⣐⣏⣎⣍⣌⣋⣊⣉⣈⣇⣆⣅⣄⣃⣂╭╮╰╯│─' for c in stripped):
            continue
        clean.append(line)
    # 去掉首尾空行
    text = '\n'.join(clean).strip()
    # 压缩连续空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text


# ============ Kiro CLI 调用 ============
kiro_bin = shutil.which("kiro-cli") or "/home/ubuntu/.local/bin/kiro-cli"

def call_kiro_simple(prompt: str) -> str:
    """简单调用（供定时任务使用，无 session 管理）"""
    log.info(f"调用 kiro-cli (simple): {prompt[:80]}...")
    try:
        cmd = [kiro_bin, "chat", "--no-interactive", "-a", "--wrap", "never"]
        if KIRO_AGENT:
            cmd += ["--agent", KIRO_AGENT]
        cmd.append(prompt)
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=KIRO_TIMEOUT,
            cwd=os.path.expanduser("~"), env={**os.environ, "NO_COLOR": "1"},
        )
        output = result.stdout.strip() or result.stderr.strip() or "Kiro 未返回结果"
        return strip_ansi(output)
    except subprocess.TimeoutExpired:
        return f"⏰ Kiro 处理超时（{KIRO_TIMEOUT}s）"
    except Exception as e:
        return f"❌ Kiro 调用失败: {e}"


# ============ 定时任务调度器 ============
task_scheduler = Scheduler(send_fn=send_message, kiro_fn=call_kiro_simple)

# ============ 会话路由 & 执行引擎 ============
session_router = SessionRouter(kiro_bin=kiro_bin, kiro_agent=KIRO_AGENT)
kiro_executor = KiroExecutor(agent=KIRO_AGENT)


# ============ 异步处理 ============
def handle_user_message(message_id: str, user_id: str, user_text: str):
    # ---- 已有命令 ----
    if user_text.startswith("/schedule"):
        args = user_text[len("/schedule"):].strip()
        reply_message(message_id, task_scheduler.handle_command(user_id, args or "help"))
        return
    if user_text.startswith("/memory"):
        if not ENABLE_MEMORY:
            reply_message(message_id, "🧠 记忆功能未启用。")
            return
        args = user_text[len("/memory"):].strip().lower()
        reply_message(message_id, handle_memory_command(user_id, args))
        return

    # ---- 新增命令 ----
    if user_text.strip() == "/new":
        session_router.clear_active(user_id)
        reply_message(message_id, "🆕 已切换到新会话模式，下条消息将开启新对话。")
        return
    if user_text.strip().startswith("/resume"):
        parts = user_text.strip().split()
        if len(parts) < 2:
            reply_message(message_id, "用法：/resume <编号>\n发送 /sessions 查看可用会话。")
            return
        try:
            short_id = int(parts[1].lstrip("#"))
        except ValueError:
            reply_message(message_id, "❌ 请输入数字编号，如 /resume 1")
            return
        session = session_router.get_by_short_id(user_id, short_id)
        if not session:
            reply_message(message_id, f"❌ 未找到会话 #{short_id}，发送 /sessions 查看列表。")
            return
        session_router.touch(user_id, session["kiro_session_id"])
        reply_message(message_id, f"🔄 已恢复会话 #{short_id} {session['topic']}\n继续发消息即可。")
        return
    if user_text.strip() == "/sessions":
        reply_message(message_id, session_router.list_sessions(user_id))
        return
    if user_text.strip() == "/status":
        status = kiro_executor.get_status(user_id)
        reply_message(message_id, status or "没有正在运行的后台任务。")
        return
    if user_text.strip() == "/cancel":
        reply_message(message_id, kiro_executor.cancel(user_id))
        return

    # ---- 检查是否有后台任务在跑 ----
    if kiro_executor.is_busy(user_id):
        reply_message(message_id, "⏳ 上一个任务还在后台运行中，请等待完成或发送 /cancel 取消。")
        return

    reply_message(message_id, "🤖 正在处理，请稍候...")

    # ---- 记忆处理 ----
    mem_enabled = ENABLE_MEMORY and memory and memory.is_enabled(user_id)
    if mem_enabled:
        memory.add(user_id, f"用户说：{user_text}")
    memories = memory.search(user_id, user_text) if mem_enabled else []
    if memories:
        prompt = "关于这个用户的已知信息：\n" + "\n".join(f"- {m}" for m in memories) + f"\n\n用户消息：{user_text}"
    else:
        prompt = user_text

    # ---- 会话路由 ----
    session_id = session_router.resolve(user_id, user_text)
    is_new = session_id is None

    # ---- 回调函数 ----
    def on_sync_result(output: str):
        _deliver_result(message_id, user_id, user_text, output, session_id, is_new, mem_enabled)

    def on_async_start():
        reply_message(message_id, "⏳ 任务较复杂，已转入后台处理。完成后会主动推送结果。\n发送 /status 查看进度，/cancel 取消。")

    def on_async_result(output: str):
        _deliver_result(message_id, user_id, user_text, output, session_id, is_new, mem_enabled)

    def on_progress(msg: str):
        send_message(user_id, msg)

    # ---- 执行 ----
    kiro_executor.execute(prompt, session_id, user_id, on_sync_result, on_async_start, on_async_result, on_progress)


def _deliver_result(message_id, user_id, user_text, output, session_id, is_new, mem_enabled):
    """统一的结果投递：回复文本 + 文件 + session 更新 + 记忆"""
    if is_new:
        session_router.register_new(user_id, user_text[:30])
        sessions = session_router._data.get(user_id, [])
        sid = sessions[-1]["kiro_session_id"] if sessions else None
    else:
        sid = session_id
        session_router.touch(user_id, session_id)

    suffix = ""
    if has_decision_signal(output):
        suffix += "\n\n💡 回复消息继续当前对话（自动延续上下文）"
    if sid:
        suffix += session_router.get_active_label(user_id, sid)

    reply_message(message_id, output + suffix)

    images, files = extract_file_paths(output)
    for img_path in images:
        key = upload_image(img_path)
        if key:
            reply_image(message_id, key)
    for file_path in files:
        key = upload_file(file_path)
        if key:
            reply_file(message_id, key)

    if mem_enabled:
        conversation = f"用户：{user_text}\n助手：{output}"
        threading.Thread(target=memory.extract_and_store, args=(user_id, conversation), daemon=True).start()


def handle_memory_command(user_id: str, args: str) -> str:
    if args == "off":
        memory.set_enabled(user_id, False)
        return "🧠 记忆功能已关闭。后续对话不会存储或检索记忆。\n发送 /memory on 可重新开启。"
    elif args == "on":
        memory.set_enabled(user_id, True)
        return "🧠 记忆功能已开启。对话将自动存储和检索记忆。"
    elif args == "clear":
        memory.clear(user_id)
        return f"🗑️ 已清除你的所有记忆。"
    elif args == "status":
        enabled = memory.is_enabled(user_id)
        all_mem = memory.list_all(user_id)
        status = "开启 ✅" if enabled else "关闭 ❌"
        return f"🧠 记忆状态：{status}\n📊 记忆条数：{len(all_mem)}"
    else:
        return (
            "🧠 记忆管理命令：\n"
            "/memory status - 查看记忆状态\n"
            "/memory on     - 开启记忆\n"
            "/memory off    - 关闭记忆\n"
            "/memory clear  - 清除所有记忆"
        )


# ============ 事件处理 ============
def on_message_receive(data: P2ImMessageReceiveV1) -> None:
    message = data.event.message
    message_id = message.message_id
    msg_type = message.message_type

    # 去重
    with _processed_lock:
        if message_id in _processed:
            return
        _processed.add(message_id)
        if len(_processed) > 1000:
            _processed.clear()

    # 只处理文本
    if msg_type != "text":
        reply_message(message_id, "目前只支持文本消息哦 📝")
        return

    # 解析文本
    try:
        content = json.loads(message.content or "{}")
        user_text = content.get("text", "").strip()
    except json.JSONDecodeError:
        user_text = ""

    if not user_text:
        return

    # 去掉 @机器人 mention
    if data.event.message.mentions:
        for m in data.event.message.mentions:
            if m.key:
                user_text = user_text.replace(m.key, "").strip()

    if not user_text:
        reply_message(message_id, "请输入您的问题 🤔")
        return

    log.info(f"用户消息: {user_text}")
    user_id = data.event.sender.sender_id.open_id or "unknown"
    t = threading.Thread(target=handle_user_message, args=(message_id, user_id, user_text))
    t.daemon = True
    t.start()


# ============ 启动 ============
if __name__ == "__main__":
    if not APP_ID or not APP_SECRET:
        log.error("⚠️  FEISHU_APP_ID / FEISHU_APP_SECRET 未设置")
        exit(1)

    # 注册事件处理器
    handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(on_message_receive) \
        .build()

    # WebSocket 长连接模式启动
    cli = lark.ws.Client(APP_ID, APP_SECRET, event_handler=handler, log_level=lark.LogLevel.INFO)
    log.info("🚀 飞书-Kiro 桥接服务启动（WebSocket 长连接模式，无需公网IP）")
    cli.start()
