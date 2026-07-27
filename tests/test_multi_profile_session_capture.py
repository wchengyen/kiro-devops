from pathlib import Path
import threading
from unittest.mock import Mock

import pytest

from multi_profile.session_capture import (
    SessionBaseline,
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


def test_begin_snapshots_current_session_ids(tmp_path):
    coordinator = SessionCaptureCoordinator()

    baseline = coordinator.begin(tmp_path, list_session_ids=lambda: {"old-a", "old-b"})

    assert baseline == SessionBaseline(frozenset({"old-a", "old-b"}))


def test_capture_returns_exact_single_new_session_after_exit(tmp_path):
    coordinator = SessionCaptureCoordinator(timeout=30, poll_interval=0, sleep=lambda _: None)
    baseline = coordinator.begin(tmp_path, list_session_ids=lambda: {"old"})

    session_id = coordinator.capture(
        tmp_path,
        baseline,
        list_session_ids=lambda: {"old", "new-session"},
    )

    assert session_id == "new-session"


def test_capture_polls_until_row_appears_after_exit(tmp_path):
    snapshots = iter([
        {"old"},           # begin
        {"old"},           # row not yet persisted (DB lags process exit)
        {"old", "new"},    # row appears a poll later
    ])
    coordinator = SessionCaptureCoordinator(timeout=30, poll_interval=0, sleep=lambda _: None)
    baseline = coordinator.begin(tmp_path, list_session_ids=lambda: next(snapshots))

    session_id = coordinator.capture(
        tmp_path,
        baseline,
        list_session_ids=lambda: next(snapshots),
    )

    assert session_id == "new"


def test_capture_raises_ambiguous_when_multiple_new_sessions(tmp_path):
    coordinator = SessionCaptureCoordinator(timeout=30, poll_interval=0, sleep=lambda _: None)
    baseline = coordinator.begin(tmp_path, list_session_ids=lambda: {"old"})

    with pytest.raises(SessionCaptureError, match="ambiguous"):
        coordinator.capture(
            tmp_path,
            baseline,
            list_session_ids=lambda: {"old", "new-a", "new-b"},
        )

    # fail closed：未綁定任何 session
    assert coordinator._claimed_for(tmp_path) == set()


def test_capture_raises_not_persisted_on_zero_new_within_timeout(tmp_path):
    times = iter([0.0, 0.0, 31.0])
    coordinator = SessionCaptureCoordinator(
        timeout=30,
        poll_interval=0,
        clock=lambda: next(times),
        sleep=lambda _: None,
    )
    baseline = coordinator.begin(tmp_path, list_session_ids=lambda: {"old"})

    with pytest.raises(SessionCaptureError, match="not persisted"):
        coordinator.capture(tmp_path, baseline, list_session_ids=lambda: {"old"})

    assert coordinator._claimed_for(tmp_path) == set()


def test_capture_wraps_listing_failure(tmp_path):
    coordinator = SessionCaptureCoordinator(timeout=30, poll_interval=0, sleep=lambda _: None)
    baseline = coordinator.begin(tmp_path, list_session_ids=lambda: {"old"})

    def failing_list():
        raise RuntimeError("list failed")

    with pytest.raises(SessionCaptureError, match="session listing failed"):
        coordinator.capture(tmp_path, baseline, list_session_ids=failing_list)


def test_interleaved_new_sessions_bind_via_claim_tracking(tmp_path):
    # chat A 與 chat B 在相同工作目錄重疊：兩者 begin 都看到同一 baseline，
    # A 先退出並 claim A；B 退出時扣除已 claim 的 A，正確綁定 B。
    coordinator = SessionCaptureCoordinator(timeout=30, poll_interval=0, sleep=lambda _: None)
    baseline_a = coordinator.begin(tmp_path, list_session_ids=lambda: {"old"})
    baseline_b = coordinator.begin(tmp_path, list_session_ids=lambda: {"old"})

    session_a = coordinator.capture(
        tmp_path,
        baseline_a,
        list_session_ids=lambda: {"old", "session-a"},
    )
    session_b = coordinator.capture(
        tmp_path,
        baseline_b,
        list_session_ids=lambda: {"old", "session-a", "session-b"},
    )

    assert session_a == "session-a"
    assert session_b == "session-b"


def test_interleaved_capture_excludes_claimed_while_polling(tmp_path):
    # B capture 第一次輪詢時只見已 claim 的 A（B 的 row 尚未落盤），
    # 不得誤認 A，需繼續輪詢直到 B 出現。
    coordinator = SessionCaptureCoordinator(timeout=30, poll_interval=0, sleep=lambda _: None)
    baseline_a = coordinator.begin(tmp_path, list_session_ids=lambda: {"old"})
    baseline_b = coordinator.begin(tmp_path, list_session_ids=lambda: {"old"})
    assert coordinator.capture(
        tmp_path, baseline_a, list_session_ids=lambda: {"old", "session-a"}
    ) == "session-a"

    snapshots_b = iter([
        {"old", "session-a"},
        {"old", "session-a", "session-b"},
    ])
    session_b = coordinator.capture(
        tmp_path, baseline_b, list_session_ids=lambda: next(snapshots_b)
    )

    assert session_b == "session-b"


def test_simultaneous_exit_ambiguity_fails_closed_without_binding(tmp_path):
    # 兩個 chat 在同一輪詢窗口內同時落盤且皆未 claim：歧義，拒絕綁定。
    coordinator = SessionCaptureCoordinator(timeout=30, poll_interval=0, sleep=lambda _: None)
    baseline_a = coordinator.begin(tmp_path, list_session_ids=lambda: {"old"})
    baseline_b = coordinator.begin(tmp_path, list_session_ids=lambda: {"old"})
    listing = lambda: {"old", "session-a", "session-b"}

    with pytest.raises(SessionCaptureError, match="ambiguous"):
        coordinator.capture(tmp_path, baseline_a, list_session_ids=listing)
    with pytest.raises(SessionCaptureError, match="ambiguous"):
        coordinator.capture(tmp_path, baseline_b, list_session_ids=listing)

    assert coordinator._claimed_for(tmp_path) == set()


def test_concurrent_interleaved_chats_in_same_dir_bind_correctly(tmp_path):
    coordinator = SessionCaptureCoordinator(timeout=30, poll_interval=0, sleep=lambda _: None)
    baseline_a = coordinator.begin(tmp_path, list_session_ids=lambda: {"old"})
    baseline_b = coordinator.begin(tmp_path, list_session_ids=lambda: {"old"})
    results = {}
    errors = []

    def run(name, baseline, listing):
        try:
            results[name] = coordinator.capture(
                tmp_path, baseline, list_session_ids=listing
            )
        except SessionCaptureError as exc:  # pragma: no cover - 失敗時由斷言呈現
            errors.append(exc)

    first = threading.Thread(
        target=run,
        args=("a", baseline_a, lambda: {"old", "session-a"}),
    )
    second = threading.Thread(
        target=run,
        args=("b", baseline_b, lambda: {"old", "session-a", "session-b"}),
    )
    first.start()
    first.join(timeout=5)
    second.start()
    second.join(timeout=5)

    assert errors == []
    assert results == {"a": "session-a", "b": "session-b"}


def test_different_directories_have_independent_claims(tmp_path):
    coordinator = SessionCaptureCoordinator(timeout=30, poll_interval=0, sleep=lambda _: None)
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    baseline_a = coordinator.begin(dir_a, list_session_ids=lambda: {"old"})
    baseline_b = coordinator.begin(dir_b, list_session_ids=lambda: {"old"})

    assert coordinator.capture(
        dir_a, baseline_a, list_session_ids=lambda: {"old", "shared-id"}
    ) == "shared-id"
    # 不同工作目錄的 claim 互不干擾
    assert coordinator.capture(
        dir_b, baseline_b, list_session_ids=lambda: {"old", "shared-id"}
    ) == "shared-id"


def test_lock_is_released_after_capture_failure(tmp_path):
    coordinator = SessionCaptureCoordinator(timeout=30, poll_interval=0, sleep=lambda _: None)
    baseline = coordinator.begin(tmp_path, list_session_ids=lambda: {"old"})

    def failing_list():
        raise RuntimeError("boom")

    with pytest.raises(SessionCaptureError):
        coordinator.capture(tmp_path, baseline, list_session_ids=failing_list)

    # 後續 capture 不受失敗影響（鎖已釋放、無殘留 claim）
    assert coordinator.capture(
        tmp_path, baseline, list_session_ids=lambda: {"old", "new"}
    ) == "new"


def test_lock_key_uses_canonical_working_directory(tmp_path):
    coordinator = SessionCaptureCoordinator()

    assert coordinator._lock_for(tmp_path) is coordinator._lock_for(tmp_path / ".")
