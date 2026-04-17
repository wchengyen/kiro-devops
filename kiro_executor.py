"""混合同步/异步 Kiro CLI 执行引擎"""
import logging
import os
import re
import signal
import subprocess
import shutil
import threading
import time
from typing import Callable

log = logging.getLogger("kiro-executor")

SYNC_TIMEOUT = 120   # Phase 1 同步等待
ASYNC_TIMEOUT = 600  # Phase 2 异步最长等待

DECISION_SIGNALS = [
    "选哪种方式", "选哪个", "你倾向哪个",
    "请审查", "请确认", "你想怎么做",
    "Choose", "Which do you", "What do you prefer",
]

kiro_bin = shutil.which("kiro-cli") or "/home/ubuntu/.local/bin/kiro-cli"


def strip_ansi(text: str) -> str:
    """去除 ANSI 转义码"""
    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z?]', '', text)
    text = re.sub(r'\x1b\].*?\x07', '', text)
    lines = text.split('\n')
    clean = []
    for line in lines:
        stripped = line.strip()
        if any(skip in stripped for skip in [
            'All tools are now trusted', 'understand the risks',
            'Learn more at', 'Credits:', '/model', '/prompts', 'Did you know'
        ]):
            continue
        if stripped and all(c in '⠀⢀⢴⢶⢦⡀⢾⠁⠈⠙⢿⡆⠰⠋⠸⣇⡿⠻⣧⠹⢷⡄⠘⣆⠻⠿⣟⢠⡁⠹⢼⠇⡸⣄⢁⢤⠉⡇⠃⠂⠐⠒⠲⠶⠤⠖⠛⠏⠗⠞⠝⠜⠚⠘⠙⠑⠊⠉⠋⠌⠍⠎⠏⡏⡇⡆⡅⡄⡃⡂⡁⡀⢿⢿⢽⢻⢺⢹⢸⢷⢵⢳⢲⢱⢰⢯⢮⢭⢬⢫⢪⢩⢨⢧⢥⢤⢣⢢⢡⢠⢟⢞⢝⢜⢛⢚⢙⢘⢗⢖⢕⢔⢓⢒⢑⢐⢏⢎⢍⢌⢋⢊⢉⢈⢇⢆⢅⢄⢃⢂╭╮╰╯│─' for c in stripped):
            continue
        clean.append(line)
    text = '\n'.join(clean).strip()
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text


def has_decision_signal(text: str) -> bool:
    """检测输出中是否包含需要用户决策的信号"""
    return any(sig in text for sig in DECISION_SIGNALS)


class KiroExecutor:
    def __init__(self, agent: str = ""):
        self._agent = agent
        self._running: dict[str, dict] = {}  # user_id -> task info
        self._lock = threading.Lock()

    def is_busy(self, user_id: str) -> bool:
        with self._lock:
            return user_id in self._running

    def get_status(self, user_id: str) -> str | None:
        with self._lock:
            task = self._running.get(user_id)
            if not task:
                return None
            elapsed = int(time.time() - task["start_time"])
            return f"⏳ 后台任务运行中（{elapsed}s）\n指令：{task['prompt'][:50]}..."

    def cancel(self, user_id: str) -> str:
        with self._lock:
            task = self._running.pop(user_id, None)
        if not task:
            return "没有正在运行的后台任务"
        try:
            task["process"].kill()
        except Exception:
            pass
        return "✅ 后台任务已取消"

    def execute(self, prompt: str, session_id: str | None, user_id: str,
                on_sync_result: Callable[[str], None],
                on_async_start: Callable[[], None],
                on_async_result: Callable[[str], None]) -> None:
        """
        混合执行：
        - on_sync_result: 同步完成时回调
        - on_async_start: 超时转异步时回调（通知用户）
        - on_async_result: 异步完成时回调（推送结果）
        """
        cmd = [kiro_bin, "chat", "--no-interactive", "-a", "--wrap", "never"]
        if session_id:
            cmd += ["--resume-id", session_id]
        if self._agent:
            cmd += ["--agent", self._agent]
        cmd.append(prompt)

        log.info(f"执行 kiro: session={session_id or 'new'}, prompt={prompt[:60]}...")

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=os.path.expanduser("~"),
            env={**os.environ, "NO_COLOR": "1"},
        )

        # Phase 1: 同步等待
        try:
            stdout, stderr = proc.communicate(timeout=SYNC_TIMEOUT)
            output = strip_ansi(stdout.strip() or stderr.strip() or "Kiro 未返回结果")
            on_sync_result(output)
            return
        except subprocess.TimeoutExpired:
            pass

        # Phase 2: 转异步
        log.info(f"同步超时，转异步模式: user={user_id}")
        with self._lock:
            self._running[user_id] = {
                "process": proc, "start_time": time.time(), "prompt": prompt,
            }
        on_async_start()

        def wait_async():
            try:
                stdout, stderr = proc.communicate(timeout=ASYNC_TIMEOUT - SYNC_TIMEOUT)
                output = strip_ansi(stdout.strip() or stderr.strip() or "Kiro 未返回结果")
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                output = f"⏰ 任务超时（{ASYNC_TIMEOUT}s），已终止"
            finally:
                with self._lock:
                    self._running.pop(user_id, None)
            on_async_result(output)

        threading.Thread(target=wait_async, daemon=True).start()
