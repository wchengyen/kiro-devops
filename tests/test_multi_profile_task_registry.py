from unittest.mock import Mock

import pytest

from multi_profile.task_registry import TaskAlreadyRunning, TaskRegistry


def test_same_principal_cannot_reserve_twice():
    registry = TaskRegistry(clock=lambda: 100.0)
    registry.reserve("principal-a", "prod-cn")

    with pytest.raises(TaskAlreadyRunning):
        registry.reserve("principal-a", "prod-cn")


def test_different_principals_can_run_in_parallel():
    registry = TaskRegistry(clock=lambda: 100.0)

    first = registry.reserve("principal-a", "prod-cn")
    second = registry.reserve("principal-b", "prod-cn")

    assert first != second
    assert registry.is_busy("principal-a") is True
    assert registry.is_busy("principal-b") is True


def test_finish_only_removes_matching_token():
    registry = TaskRegistry(clock=lambda: 100.0)
    token = registry.reserve("principal-a", "prod-cn")

    registry.finish("principal-a", "wrong-token")
    assert registry.is_busy("principal-a") is True

    registry.finish("principal-a", token)
    assert registry.is_busy("principal-a") is False


def test_cancel_returns_handle_without_logging_prompt():
    registry = TaskRegistry(clock=lambda: 100.0)
    token = registry.reserve("principal-a", "prod-cn")
    process = Mock()
    assert registry.attach("principal-a", token, process) is False

    handle = registry.request_cancel("principal-a")

    assert handle.token == token
    assert handle.process is process
    assert registry.status("principal-a") == "prod-cn task cancelling (0s)"


def test_cancel_before_attach_is_reported_to_attacher():
    registry = TaskRegistry(clock=lambda: 100.0)
    token = registry.reserve("principal-a", "prod-cn")

    handle = registry.request_cancel("principal-a")
    assert handle.token == token
    assert handle.process is None
    assert registry.attach("principal-a", token, Mock()) is True


def test_cancel_wins_over_normal_completion():
    registry = TaskRegistry(clock=lambda: 100.0)
    token = registry.reserve("principal-a", "prod-cn")
    registry.request_cancel("principal-a")

    assert registry.claim_completion("principal-a", token) is False


def test_normal_completion_wins_before_late_cancel():
    registry = TaskRegistry(clock=lambda: 100.0)
    token = registry.reserve("principal-a", "prod-cn")

    assert registry.claim_completion("principal-a", token) is True
    assert registry.request_cancel("principal-a") is None


def test_repeated_cancel_returns_same_handle():
    registry = TaskRegistry(clock=lambda: 100.0)
    token = registry.reserve("principal-a", "prod-cn")
    process = Mock()
    registry.attach("principal-a", token, process)

    first = registry.request_cancel("principal-a")
    second = registry.request_cancel("principal-a")

    assert first == second


def test_should_cancel_detects_cancelled_or_removed_reservation():
    registry = TaskRegistry(clock=lambda: 100.0)
    token = registry.reserve("principal-a", "prod-cn")
    assert registry.should_cancel("principal-a", token) is False

    registry.request_cancel("principal-a")
    assert registry.should_cancel("principal-a", token) is True

    registry.finish("principal-a", token)
    assert registry.should_cancel("principal-a", token) is True


def test_counts_by_profile_and_total_running():
    registry = TaskRegistry(clock=lambda: 100.0)
    registry.reserve("principal-a", "prod-cn")
    registry.reserve("principal-b", "prod-cn")
    token_c = registry.reserve("principal-c", "eu")

    assert registry.counts_by_profile() == {"prod-cn": 2, "eu": 1}
    assert registry.total_running() == 3

    registry.finish("principal-c", token_c)
    assert registry.counts_by_profile() == {"prod-cn": 2}
    assert registry.total_running() == 2
