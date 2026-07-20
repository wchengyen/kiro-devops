from unittest.mock import Mock

from multi_profile.process_utils import terminate_process_tree


def test_terminate_process_tree_kills_group_and_waits():
    process = Mock(pid=123)
    getpgid = Mock(return_value=456)
    killpg = Mock()

    terminate_process_tree(process, getpgid=getpgid, killpg=killpg)

    killpg.assert_called_once_with(456, 9)
    process.wait.assert_called_once_with()
    process.kill.assert_not_called()


def test_terminate_process_tree_falls_back_to_parent_kill():
    process = Mock(pid=123)

    terminate_process_tree(
        process,
        getpgid=Mock(side_effect=OSError("gone")),
        killpg=Mock(),
    )

    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with()
