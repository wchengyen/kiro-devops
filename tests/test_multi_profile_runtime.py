import subprocess
import threading
from unittest.mock import Mock

import pytest

from multi_profile.models import ExecutionContext, ProfileConfig, build_profile_fingerprint
from multi_profile.runtime import ContextRuntime, RuntimeFailure
from multi_profile.session_capture import CapturedSession, SessionCaptureError
from multi_profile.session_store import SessionStore
from multi_profile.task_registry import TaskRegistry


class FakeProcess:
    def __init__(self, outputs=("ok", ""), timeout_once=False, returncode=0):
        self.outputs = outputs
        self.timeout_once = timeout_once
        self.returncode = returncode
        self.calls = 0
        self.pid = 123
        self.killed = False

    def communicate(self, timeout=None):
        self.calls += 1
        if self.timeout_once and self.calls == 1:
            raise subprocess.TimeoutExpired("kiro", timeout)
        return self.outputs

    def kill(self):
        self.killed = True

    def wait(self):
        return 0


def make_context(tmp_path, principal="principal-a"):
    profile = ProfileConfig(
        profile_id="prod-cn",
        aws_profile="production",
        expected_account_id="123456789012",
        working_dir=str(tmp_path),
        sync_timeout=10,
        async_timeout=30,
    )
    return ExecutionContext(
        config_generation=1,
        platform="feishu",
        app_key="ops-bot",
        chat_type="group",
        chat_id="oc_prod",
        user_id="ou_user",
        principal_key=principal,
        group_scope_key="feishu/ops-bot/group/oc_prod",
        profile_id=profile.profile_id,
        profile=profile,
        profile_fingerprint=build_profile_fingerprint(profile),
    )


def make_runtime(tmp_path, process, capture=None, popen=None):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    return ContextRuntime(
        kiro_bin="/usr/bin/kiro-cli",
        session_store=store,
        session_capture=capture or Mock(),
        task_registry=TaskRegistry(clock=lambda: 100.0),
        popen_factory=popen or Mock(return_value=process),
        list_session_ids=lambda context, env: set(),
        clock=lambda: 100.0,
    ), store


def callbacks():
    return {
        "on_sync_result": Mock(),
        "on_async_start": Mock(),
        "on_async_result": Mock(),
        "on_error": Mock(),
        "on_progress": Mock(),
    }


def test_new_session_is_captured_registered_and_returned_sync(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess(("answer", ""))
    capture = Mock()
    capture.start_and_capture.return_value = CapturedSession("session-new", process)
    runtime, store = make_runtime(tmp_path, process, capture=capture)
    cb = callbacks()

    runtime.execute(context, "hello", **cb)

    cb["on_sync_result"].assert_called_once_with("answer")
    cb["on_async_start"].assert_not_called()
    record = store.resolve_active(context, now=100.0)
    assert record.kiro_session_id == "session-new"
    assert record.message_count == 1
    assert runtime.is_busy(context) is False


def test_existing_session_uses_resume_id_and_touches_record(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess(("continued", ""))
    popen = Mock(return_value=process)
    runtime, store = make_runtime(tmp_path, process, popen=popen)
    store.register_new(context, "session-existing", "topic", now=50.0)
    cb = callbacks()

    runtime.execute(context, "continue", **cb)

    command = popen.call_args.args[0]
    assert command[command.index("--resume-id") + 1] == "session-existing"
    assert store.resolve_active(context, now=100.0).message_count == 2
    cb["on_sync_result"].assert_called_once_with("continued")


def test_runtime_uses_context_working_directory_and_isolated_env(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess()
    popen = Mock(return_value=process)
    runtime, store = make_runtime(tmp_path, process, popen=popen)
    store.register_new(context, "session-existing", "topic", now=50.0)

    runtime.execute(context, "hello", **callbacks())

    assert popen.call_args.kwargs["cwd"] == str(tmp_path)
    assert popen.call_args.kwargs["env"]["AWS_PROFILE"] == "production"
    assert popen.call_args.kwargs["start_new_session"] is True


def test_nonzero_process_exit_reports_typed_error_without_touch(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess(("", "access denied"), returncode=7)
    runtime, store = make_runtime(tmp_path, process, popen=Mock(return_value=process))
    record = store.register_new(context, "session-existing", "topic", now=50.0)
    cb = callbacks()

    runtime.execute(context, "hello", **cb)

    failure = cb["on_error"].call_args.args[0]
    assert failure == RuntimeFailure("process_failed", "access denied", 7)
    assert store.get_by_short_id(context, record.short_id).message_count == 1
    cb["on_sync_result"].assert_not_called()


def test_session_list_nonzero_exit_is_fail_closed(tmp_path, monkeypatch):
    context = make_context(tmp_path)
    process = FakeProcess()
    runtime, _ = make_runtime(tmp_path, process)
    runtime._list_session_ids_fn = None
    monkeypatch.setattr(
        "multi_profile.runtime.subprocess.run",
        Mock(return_value=Mock(returncode=2, stdout="", stderr="failed")),
    )

    with pytest.raises(SessionCaptureError, match="session listing exited with 2"):
        runtime._list_session_ids(context, {})


def test_session_registration_failure_terminates_process_and_releases_task(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess()
    capture = Mock()
    capture.start_and_capture.return_value = CapturedSession("session-new", process)
    runtime, store = make_runtime(tmp_path, process, capture=capture)
    runtime._terminate_process = Mock()
    store.register_new = Mock(side_effect=OSError("db failed"))
    cb = callbacks()

    runtime.execute(context, "hello", **cb)

    runtime._terminate_process.assert_called_once_with(process)
    assert runtime.is_busy(context) is False
    assert cb["on_error"].call_args.args[0].code == "startup_failed"


def test_communicate_exception_terminates_process_and_reports_error(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess()
    process.communicate = Mock(side_effect=OSError("pipe failed"))
    runtime, store = make_runtime(tmp_path, process, popen=Mock(return_value=process))
    store.register_new(context, "session-existing", "topic", now=50.0)
    runtime._terminate_process = Mock()
    cb = callbacks()

    runtime.execute(context, "hello", **cb)

    runtime._terminate_process.assert_called_once_with(process)
    assert cb["on_error"].call_args.args[0].code == "execution_failed"
    assert runtime.is_busy(context) is False


def test_runtime_reservation_blocks_same_principal_before_process_start(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess()
    runtime, _ = make_runtime(tmp_path, process)
    token = runtime.task_registry.reserve(context.principal_key, context.profile_id)

    try:
        assert runtime.is_busy(context) is True
    finally:
        runtime.task_registry.finish(context.principal_key, token)


class InlineThread:
    def __init__(self, target, daemon=True, **kwargs):
        self.target = target

    def start(self):
        self.target()


def test_timeout_transitions_to_async_and_delivers_result(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess(("async answer", ""), timeout_once=True)
    popen = Mock(return_value=process)
    runtime, store = make_runtime(tmp_path, process, popen=popen)
    runtime._thread_factory = InlineThread
    store.register_new(context, "session-existing", "topic", now=50.0)
    cb = callbacks()

    runtime.execute(context, "long task", **cb)

    cb["on_async_start"].assert_called_once_with()
    cb["on_async_result"].assert_called_once_with("async answer")
    cb["on_sync_result"].assert_not_called()
    assert runtime.is_busy(context) is False


def test_async_final_timeout_terminates_process_tree(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess(timeout_once=True)

    def always_timeout(timeout=None):
        raise subprocess.TimeoutExpired("kiro", timeout)

    process.communicate = always_timeout
    runtime, store = make_runtime(tmp_path, process, popen=Mock(return_value=process))
    runtime._thread_factory = InlineThread
    runtime._terminate_process = Mock()
    store.register_new(context, "session-existing", "topic", now=50.0)
    cb = callbacks()

    runtime.execute(context, "too long", **cb)

    runtime._terminate_process.assert_called_once_with(process)
    assert cb["on_error"].call_args.args[0] == RuntimeFailure(
        "async_timeout",
        "任務超時（30s），已終止",
    )
    cb["on_async_result"].assert_not_called()


def test_cancel_terminates_only_matching_principal(tmp_path):
    first = make_context(tmp_path, "principal-a")
    second = make_context(tmp_path, "principal-b")
    process_a = FakeProcess()
    process_b = FakeProcess()
    runtime, _ = make_runtime(tmp_path, process_a)
    token_a = runtime.task_registry.reserve(first.principal_key, first.profile_id)
    token_b = runtime.task_registry.reserve(second.principal_key, second.profile_id)
    runtime.task_registry.attach(first.principal_key, token_a, process_a)
    runtime.task_registry.attach(second.principal_key, token_b, process_b)
    runtime._terminate_process = Mock()

    assert runtime.cancel(first) is True

    runtime._terminate_process.assert_called_once_with(process_a)
    assert runtime.is_busy(second) is True
    runtime.task_registry.finish(second.principal_key, token_b)


def test_async_progress_uses_elapsed_minutes_without_prompt(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess(timeout_once=True)
    calls = iter([
        subprocess.TimeoutExpired("kiro", 10),
        subprocess.TimeoutExpired("kiro", 10),
        ("done", ""),
    ])

    def communicate(timeout=None):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    process.communicate = communicate
    runtime, store = make_runtime(tmp_path, process, popen=Mock(return_value=process))
    runtime._thread_factory = InlineThread
    runtime._progress_interval = 10
    store.register_new(context, "session-existing", "topic", now=50.0)
    cb = callbacks()

    runtime.execute(context, "sensitive prompt", **cb)

    cb["on_progress"].assert_called_once_with("仍在處理中（已運行 0 分鐘）")
    assert "sensitive prompt" not in str(cb["on_progress"].call_args)


def test_new_session_async_completion_keeps_message_count_one(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess(("done", ""), timeout_once=True)
    capture = Mock()
    capture.start_and_capture.return_value = CapturedSession("session-new", process)
    runtime, store = make_runtime(tmp_path, process, capture=capture)
    runtime._thread_factory = InlineThread

    runtime.execute(context, "new async", **callbacks())

    assert store.resolve_active(context, now=100.0).message_count == 1


def test_cancel_during_session_capture_registers_no_session(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess()
    started = threading.Event()
    release = threading.Event()

    class BlockingCapture:
        def start_and_capture(self, working_dir, *, list_session_ids, start_process):
            attached = start_process()
            started.set()
            release.wait(timeout=1)
            return CapturedSession("session-new", attached)

    runtime, store = make_runtime(tmp_path, process, capture=BlockingCapture())
    runtime._terminate_process = Mock()
    cb = callbacks()
    worker = threading.Thread(target=lambda: runtime.execute(context, "hello", **cb))
    worker.start()
    assert started.wait(timeout=1)

    assert runtime.cancel(context) is True
    release.set()
    worker.join(timeout=1)

    assert store.list_sessions(context, limit=10) == []
    cb["on_sync_result"].assert_not_called()
    cb["on_async_result"].assert_not_called()
    assert runtime._terminate_process.call_count >= 1


def test_cancel_wins_between_communicate_and_completion_claim(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess()
    runtime, store = make_runtime(tmp_path, process, popen=Mock(return_value=process))
    record = store.register_new(context, "session-existing", "topic", now=50.0)
    runtime._terminate_process = Mock()
    cb = callbacks()

    def communicate(timeout=None):
        assert runtime.cancel(context) is True
        return "late success", ""

    process.communicate = communicate
    runtime.execute(context, "hello", **cb)

    cb["on_sync_result"].assert_not_called()
    cb["on_async_result"].assert_not_called()
    assert store.get_by_short_id(context, record.short_id).message_count == 1


def test_thread_start_failure_terminates_process_and_reports_error(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess(timeout_once=True)
    runtime, store = make_runtime(tmp_path, process, popen=Mock(return_value=process))
    store.register_new(context, "session-existing", "topic", now=50.0)
    runtime._terminate_process = Mock()

    class FailingThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("thread failed")

    runtime._thread_factory = FailingThread
    cb = callbacks()
    runtime.execute(context, "hello", **cb)

    runtime._terminate_process.assert_called_once_with(process)
    assert cb["on_error"].call_args.args[0].code == "async_start_failed"
    assert runtime.is_busy(context) is False


def test_progress_callback_failure_terminates_process_and_reports_error(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess(timeout_once=True)
    process.communicate = Mock(side_effect=[
        subprocess.TimeoutExpired("kiro", 10),
        subprocess.TimeoutExpired("kiro", 10),
    ])
    runtime, store = make_runtime(tmp_path, process, popen=Mock(return_value=process))
    store.register_new(context, "session-existing", "topic", now=50.0)
    runtime._thread_factory = InlineThread
    runtime._progress_interval = 10
    runtime._terminate_process = Mock()
    cb = callbacks()
    cb["on_progress"].side_effect = RuntimeError("callback failed")

    runtime.execute(context, "hello", **cb)

    runtime._terminate_process.assert_called_once_with(process)
    assert cb["on_error"].call_args.args[0].code == "async_failed"
    assert runtime.is_busy(context) is False


def test_async_nonzero_exit_reports_error_without_touch(tmp_path):
    context = make_context(tmp_path)
    process = FakeProcess(("", "denied"), timeout_once=True, returncode=9)
    runtime, store = make_runtime(tmp_path, process, popen=Mock(return_value=process))
    record = store.register_new(context, "session-existing", "topic", now=50.0)
    runtime._thread_factory = InlineThread
    cb = callbacks()

    runtime.execute(context, "hello", **cb)

    assert cb["on_error"].call_args.args[0] == RuntimeFailure(
        "process_failed",
        "denied",
        9,
    )
    assert store.get_by_short_id(context, record.short_id).message_count == 1
