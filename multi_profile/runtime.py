from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .models import ExecutionContext
from .output import clean_output
from .process_utils import terminate_process_tree
from .runtime_env import build_child_env, build_kiro_command
from .session_capture import (
    SessionBaseline,
    SessionCaptureCoordinator,
    SessionCaptureError,
    parse_session_ids,
)
from .session_store import SessionStore
from .task_registry import TaskRegistry

log = logging.getLogger("multi-profile-runtime")


@dataclass(frozen=True)
class RuntimeFailure:
    code: str
    message: str
    returncode: int | None = None


@dataclass(frozen=True)
class _PendingSessionCapture:
    """新 session 的延後捕捉狀態：baseline 於啟動前拍攝，程序退出後才 capture。"""

    baseline: SessionBaseline
    env: dict[str, str]
    topic: str


class RuntimeCancelled(RuntimeError):
    pass


class ContextRuntime:
    def __init__(
        self,
        *,
        kiro_bin: str,
        session_store: SessionStore,
        session_capture: SessionCaptureCoordinator,
        task_registry: TaskRegistry,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        list_session_ids: Callable[[ExecutionContext, dict[str, str]], set[str]] | None = None,
        clock: Callable[[], float] = time.time,
        thread_factory: Callable[..., Any] = threading.Thread,
        progress_interval: int = 300,
        session_timeout: int = 1800,
        terminate_process: Callable[[Any], None] = terminate_process_tree,
    ):
        self.kiro_bin = kiro_bin
        self.session_store = session_store
        self.session_capture = session_capture
        self.task_registry = task_registry
        self._popen_factory = popen_factory
        self._list_session_ids_fn = list_session_ids
        self._clock = clock
        self._thread_factory = thread_factory
        self._progress_interval = progress_interval
        self._session_timeout = session_timeout
        self._terminate_process = terminate_process

    def is_busy(self, context: ExecutionContext) -> bool:
        return self.task_registry.is_busy(context.principal_key)

    def status(self, context: ExecutionContext) -> str | None:
        return self.task_registry.status(context.principal_key)

    def cancel(self, context: ExecutionContext) -> bool:
        handle = self.task_registry.request_cancel(context.principal_key)
        if handle is None:
            return False
        if handle.process is not None:
            self._terminate_process(handle.process)
            self.task_registry.finish(context.principal_key, handle.token)
        return True

    def _wait_async(
        self,
        context: ExecutionContext,
        token: str,
        process,
        session_id: str | None,
        pending: _PendingSessionCapture | None,
        *,
        on_async_result: Callable[[str], None],
        on_error: Callable[[RuntimeFailure], None],
        on_progress: Callable[[str], None] | None,
    ) -> None:
        remaining = context.profile.async_timeout - context.profile.sync_timeout
        elapsed = context.profile.sync_timeout
        try:
            while remaining > 0:
                wait = min(self._progress_interval, remaining)
                try:
                    stdout, stderr = process.communicate(timeout=wait)
                except subprocess.TimeoutExpired:
                    remaining -= wait
                    elapsed += wait
                    if remaining > 0 and on_progress is not None:
                        on_progress(f"仍在處理中（已運行 {elapsed // 60} 分鐘）")
                    continue

                self._finish_result(
                    context,
                    token,
                    process,
                    session_id,
                    pending,
                    stdout,
                    stderr,
                    on_result=on_async_result,
                    on_error=on_error,
                )
                return

            self._terminate_process(process)
            if self.task_registry.claim_completion(context.principal_key, token):
                if pending is not None:
                    self._capture_and_register(context, pending)
                on_error(
                    RuntimeFailure(
                        "async_timeout",
                        f"任務超時（{context.profile.async_timeout}s），已終止",
                    )
                )
        except Exception as exc:
            self._terminate_process(process)
            if self.task_registry.claim_completion(context.principal_key, token):
                if pending is not None:
                    self._capture_and_register(context, pending)
                on_error(RuntimeFailure("async_failed", str(exc)))
        finally:
            self.task_registry.finish(context.principal_key, token)

    def _list_session_ids(
        self,
        context: ExecutionContext,
        env: dict[str, str],
    ) -> set[str]:
        if self._list_session_ids_fn is not None:
            return self._list_session_ids_fn(context, env)
        result = subprocess.run(
            [self.kiro_bin, "chat", "--list-sessions"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=context.profile.working_dir,
            env=env,
        )
        if result.returncode != 0:
            raise SessionCaptureError(
                f"kiro session listing exited with {result.returncode}"
            )
        return parse_session_ids((result.stdout or "") + (result.stderr or ""))

    def _start_process(
        self,
        command: list[str],
        context: ExecutionContext,
        env: dict[str, str],
        token: str,
    ):
        process = self._popen_factory(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=context.profile.working_dir,
            env=env,
            start_new_session=True,
        )
        try:
            cancel_requested = self.task_registry.attach(
                context.principal_key,
                token,
                process,
            )
        except Exception as exc:
            self._terminate_process(process)
            raise RuntimeCancelled("reservation ended before process attach") from exc
        if cancel_requested:
            self._terminate_process(process)
            self.task_registry.finish(context.principal_key, token)
            raise RuntimeCancelled("task cancelled during process start")
        return process

    def _start_or_resume(
        self,
        context: ExecutionContext,
        prompt: str,
        env: dict[str, str],
        token: str,
    ):
        existing = self.session_store.resolve_active(
            context,
            now=self._clock(),
            timeout=self._session_timeout,
        )
        if existing is not None:
            command = build_kiro_command(
                self.kiro_bin,
                context,
                prompt,
                existing.kiro_session_id,
            )
            process = self._start_process(command, context, env, token)
            return process, existing.kiro_session_id, None

        # kiro-cli 只在 chat 程序退出時才把新 session 寫入 sqlite（2.4.1 實測），
        # 因此啟動前只拍 baseline，待程序退出後才 capture。
        baseline = self.session_capture.begin(
            context.profile.working_dir,
            list_session_ids=lambda: self._list_session_ids(context, env),
        )
        command = build_kiro_command(self.kiro_bin, context, prompt, None)
        process = self._start_process(command, context, env, token)
        return process, None, _PendingSessionCapture(baseline, env, prompt[:30])

    def _capture_and_register(
        self,
        context: ExecutionContext,
        pending: _PendingSessionCapture,
    ) -> None:
        """程序退出後捕捉新 session 並註冊。

        失敗（未落盤／歧義／註冊錯誤）不影響任務結果交付：本次只是不綁定
        session，下則訊息會開新 session。一律記 warning（含 trace context），
        絕不猜測 session ID。
        """
        try:
            session_id = self.session_capture.capture(
                context.profile.working_dir,
                pending.baseline,
                list_session_ids=lambda: self._list_session_ids(context, pending.env),
            )
            self.session_store.register_new(
                context,
                session_id,
                pending.topic,
                now=self._clock(),
            )
        except Exception as exc:
            log.warning(
                "Kiro session capture failed after process exit; "
                "result delivered without binding session "
                "principal=%s profile=%s working_dir=%s: %s",
                context.principal_key,
                context.profile_id,
                context.profile.working_dir,
                exc,
            )

    @staticmethod
    def _output(stdout: str, stderr: str) -> str:
        return clean_output(stdout, stderr)

    def _finish_result(
        self,
        context: ExecutionContext,
        token: str,
        process,
        session_id: str | None,
        pending: _PendingSessionCapture | None,
        stdout: str,
        stderr: str,
        *,
        on_result: Callable[[str], None],
        on_error: Callable[[RuntimeFailure], None],
    ) -> None:
        if not self.task_registry.claim_completion(context.principal_key, token):
            return
        output = self._output(stdout, stderr)
        if process.returncode not in (None, 0):
            if pending is not None:
                # best-effort：失敗的 chat 若仍有落盤 row 就綁定，否則略過
                self._capture_and_register(context, pending)
            on_error(RuntimeFailure("process_failed", output, process.returncode))
            return
        if pending is not None:
            self._capture_and_register(context, pending)
        else:
            self.session_store.touch(context, session_id, now=self._clock())
        on_result(output)

    def execute(
        self,
        context: ExecutionContext,
        prompt: str,
        *,
        on_sync_result: Callable[[str], None],
        on_async_start: Callable[[], None],
        on_async_result: Callable[[str], None],
        on_error: Callable[[RuntimeFailure], None],
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        token = self.task_registry.reserve(context.principal_key, context.profile_id)
        process = None
        pending = None
        try:
            env = build_child_env(context)
            process, session_id, pending = self._start_or_resume(
                context,
                prompt,
                env,
                token,
            )
            try:
                stdout, stderr = process.communicate(timeout=context.profile.sync_timeout)
            except subprocess.TimeoutExpired:
                try:
                    on_async_start()
                    worker = self._thread_factory(
                        target=lambda: self._wait_async(
                            context,
                            token,
                            process,
                            session_id,
                            pending,
                            on_async_result=on_async_result,
                            on_error=on_error,
                            on_progress=on_progress,
                        ),
                        daemon=True,
                        name=f"kiro-{context.profile_id}-{token[:8]}",
                    )
                    worker.start()
                except Exception as exc:
                    self._terminate_process(process)
                    if self.task_registry.claim_completion(context.principal_key, token):
                        on_error(RuntimeFailure("async_start_failed", str(exc)))
                    else:
                        self.task_registry.finish(context.principal_key, token)
                return
        except RuntimeCancelled:
            self.task_registry.finish(context.principal_key, token)
            return
        except Exception as exc:
            if process is not None:
                self._terminate_process(process)
                if pending is not None:
                    self._capture_and_register(context, pending)
            self.task_registry.finish(context.principal_key, token)
            code = "execution_failed" if process is not None else "startup_failed"
            on_error(RuntimeFailure(code, str(exc)))
            return

        self._finish_result(
            context,
            token,
            process,
            session_id,
            pending,
            stdout,
            stderr,
            on_result=on_sync_result,
            on_error=on_error,
        )
