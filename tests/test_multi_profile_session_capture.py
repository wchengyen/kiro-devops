from pathlib import Path
import threading
import time
from unittest.mock import Mock

import pytest

from multi_profile.session_capture import (
    SessionCaptureCoordinator,
    SessionCaptureError,
    parse_session_ids,
)


def test_parse_session_ids_deduplicates_uuid_values():
    text = """
    Chat SessionId: 11111111-1111-1111-1111-111111111111
    duplicate 11111111-1111-1111-1111-111111111111
    Chat SessionId: 22222222-2222-2222-2222-222222222222
    """

    assert parse_session_ids(text) == {
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    }


def test_capture_returns_exact_single_new_session(tmp_path):
    process = Mock()
    snapshots = iter([
        {"old"},
        {"old", "new-session"},
    ])
    coordinator = SessionCaptureCoordinator(timeout=30, poll_interval=0, sleep=lambda _: None)

    captured = coordinator.start_and_capture(
        tmp_path,
        list_session_ids=lambda: next(snapshots),
        start_process=lambda: process,
    )

    assert captured.session_id == "new-session"
    assert captured.process is process


def test_capture_terminates_process_tree_when_multiple_new_sessions_appear(tmp_path):
    process = Mock()
    terminate = Mock()
    snapshots = iter([
        {"old"},
        {"old", "new-a", "new-b"},
    ])
    coordinator = SessionCaptureCoordinator(
        timeout=30,
        poll_interval=0,
        sleep=lambda _: None,
        terminate_process=terminate,
    )

    with pytest.raises(SessionCaptureError, match="ambiguous"):
        coordinator.start_and_capture(
            tmp_path,
            list_session_ids=lambda: next(snapshots),
            start_process=lambda: process,
        )

    terminate.assert_called_once_with(process)


def test_capture_terminates_process_tree_on_timeout(tmp_path):
    process = Mock()
    terminate = Mock()
    times = iter([0.0, 0.0, 31.0])
    coordinator = SessionCaptureCoordinator(
        timeout=30,
        poll_interval=0,
        clock=lambda: next(times),
        sleep=lambda _: None,
        terminate_process=terminate,
    )

    with pytest.raises(SessionCaptureError, match="timed out"):
        coordinator.start_and_capture(
            tmp_path,
            list_session_ids=lambda: {"old"},
            start_process=lambda: process,
        )

    terminate.assert_called_once_with(process)


def test_polling_error_terminates_started_process(tmp_path):
    process = Mock()
    terminate = Mock()
    calls = iter([{"old"}, RuntimeError("list failed")])
    coordinator = SessionCaptureCoordinator(
        terminate_process=terminate,
        poll_interval=0,
        sleep=lambda _: None,
    )

    with pytest.raises(SessionCaptureError, match="session listing failed"):
        coordinator.start_and_capture(
            tmp_path,
            list_session_ids=lambda: (
                (_ for _ in ()).throw(value) if isinstance(value := next(calls), Exception) else value
            ),
            start_process=lambda: process,
        )

    terminate.assert_called_once_with(process)


def test_same_directory_serializes_only_until_first_uuid_is_captured(tmp_path):
    coordinator = SessionCaptureCoordinator(poll_interval=0, sleep=lambda _: None)
    first_started = threading.Event()
    allow_first_capture = threading.Event()
    second_started = threading.Event()
    results = []
    first_calls = 0

    def first_list():
        nonlocal first_calls
        first_calls += 1
        if first_calls == 1:
            return {"old"}
        allow_first_capture.wait(timeout=1)
        return {"old", "first-session"}

    second_snapshots = iter([{"old", "first-session"}, {"old", "first-session", "second-session"}])

    def run_first():
        results.append(
            coordinator.start_and_capture(
                tmp_path,
                list_session_ids=first_list,
                start_process=lambda: (first_started.set() or Mock()),
            ).session_id
        )

    def run_second():
        results.append(
            coordinator.start_and_capture(
                tmp_path / ".",
                list_session_ids=lambda: next(second_snapshots),
                start_process=lambda: (second_started.set() or Mock()),
            ).session_id
        )

    first_thread = threading.Thread(target=run_first)
    second_thread = threading.Thread(target=run_second)
    first_thread.start()
    assert first_started.wait(timeout=1)
    second_thread.start()
    assert second_started.wait(timeout=0.05) is False

    allow_first_capture.set()
    assert second_started.wait(timeout=1)
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert sorted(results) == ["first-session", "second-session"]


def test_different_directories_can_start_in_parallel(tmp_path):
    coordinator = SessionCaptureCoordinator(poll_interval=0, sleep=lambda _: None)
    first_started = threading.Event()
    second_started = threading.Event()
    release = threading.Event()

    def run(path, session_id, started):
        calls = 0

        def list_ids():
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"old"}
            release.wait(timeout=1)
            return {"old", session_id}

        coordinator.start_and_capture(
            path,
            list_session_ids=list_ids,
            start_process=lambda: (started.set() or Mock()),
        )

    first = threading.Thread(target=run, args=(tmp_path / "a", "session-a", first_started))
    second = threading.Thread(target=run, args=(tmp_path / "b", "session-b", second_started))
    first.start()
    second.start()

    assert first_started.wait(timeout=1)
    assert second_started.wait(timeout=1)
    release.set()
    first.join(timeout=1)
    second.join(timeout=1)


def test_lock_is_released_after_capture_failure(tmp_path):
    terminate = Mock()
    coordinator = SessionCaptureCoordinator(
        terminate_process=terminate,
        poll_interval=0,
        sleep=lambda _: None,
    )
    failing = iter([{"old"}, RuntimeError("boom")])

    with pytest.raises(SessionCaptureError):
        coordinator.start_and_capture(
            tmp_path,
            list_session_ids=lambda: (
                (_ for _ in ()).throw(value) if isinstance(value := next(failing), Exception) else value
            ),
            start_process=Mock,
        )

    succeeding = iter([{"old"}, {"old", "new"}])
    result = coordinator.start_and_capture(
        tmp_path,
        list_session_ids=lambda: next(succeeding),
        start_process=Mock,
    )
    assert result.session_id == "new"


def test_lock_key_uses_canonical_working_directory(tmp_path):
    coordinator = SessionCaptureCoordinator()

    assert coordinator._lock_for(tmp_path) is coordinator._lock_for(tmp_path / ".")
