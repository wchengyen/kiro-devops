# Runtime、Session 與記憶隔離實作計畫

> **勘誤（2026-07）：** 本計畫原先設計在 chat 程序運行中輪詢捕捉新 Session UUID。實測 kiro-cli 2.4.1 發現 conversation row 只在程序**退出時**才寫入 sqlite（`conversations_v2`），運行中永遠捕捉不到。已改為 capture-at-exit：`SessionCaptureCoordinator.begin()` 啟動前拍 baseline、`capture()` 退出後比對並以 per-working-dir claimed 集合去重；捕捉失敗不影響結果交付。詳見規格 §10.2。

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推薦）或 superpowers:executing-plans 逐任務實現此計畫。步驟使用復選框（`- [ ]`）語法追蹤進度。

**目標：** 基於計畫 1 的 `ExecutionContext`，建立不污染全域環境的 Kiro Runtime、精確 `--resume-id`、per-principal 任務隔離、並行安全 Session UUID 捕捉、SQLite SessionStore，並證明既有 Semantic／Event Store 使用 context key 後不會跨群洩漏。

**架構：** 新功能全部放在 `multi_profile` package，透過依賴注入隔離 subprocess、clock、sleep 與 thread。此計畫產出可由計畫 3 接線的 `ContextRuntime`，但不修改 `MessageHandler`、舊 `KiroExecutor`、舊 `SessionRouter` 或任何 Adapter；`MULTI_PROFILE_ENABLED=false` 時生產行為完全不變。

**技術棧：** Python 3.10+、標準庫 `subprocess`／`sqlite3`／`threading`／`signal`／`uuid`、pytest、計畫 1 的 `multi_profile.models`。

**依賴：** 必須先完整實作並驗證 `docs/superpowers/plans/2026-07-14-multi-profile-routing-core.md`。

**參考規格：** `docs/superpowers/specs/2026-07-14-multi-profile-multi-feishu-group-design.md` 第 8–12、15–18、21 節。

---

## 檔案結構

### 建立

- `multi_profile/runtime_env.py`：建立隔離 child env 與精確 Kiro argv；不啟動程序。
- `multi_profile/output.py`：清理 Kiro ANSI／banner 輸出，不依賴 legacy executor。
- `multi_profile/process_utils.py`：統一終止 Kiro 程序組並 wait，供 capture、cancel 與 timeout 共用。
- `multi_profile/task_registry.py`：以 `principal_key` 保留／綁定／取消單一執行中任務。
- `multi_profile/session_store.py`：SQLite Session schema、註冊、解析、恢復、過期與裁剪。
- `multi_profile/session_capture.py`：按 canonical working directory 序列化 Session UUID 配置。
- `multi_profile/runtime.py`：context-aware 同步／異步 Kiro 執行與 Session 整合。
- `multi_profile/scoped_state.py`：由 ExecutionContext 取得 semantic/event owner 並派生 scoped event ID。
- `tests/test_multi_profile_runtime_env.py`
- `tests/test_multi_profile_output.py`
- `tests/test_multi_profile_process_utils.py`
- `tests/test_multi_profile_task_registry.py`
- `tests/test_multi_profile_session_store.py`
- `tests/test_multi_profile_session_capture.py`
- `tests/test_multi_profile_runtime.py`
- `tests/test_multi_profile_memory_isolation.py`

### 修改

- `multi_profile/__init__.py`：匯出計畫 3 可依賴的穩定介面。

### 明確不修改

- `gateway.py`
- `message_handler.py`
- `kiro_executor.py`
- `session_router.py`
- `memory.py`
- `semantic_store.py`
- `event_store.py`
- `alert_analysis.py`
- `platform_dispatcher.py`
- `adapters/`
- `dashboard/`

---

## 執行前基線

- [ ] **記錄計畫 2 起始 SHA**

```bash
git rev-parse HEAD > .git/plan2-base-sha
cat .git/plan2-base-sha
```

預期：輸出計畫 1 完成後的 HEAD SHA。後續所有範圍驗證都讀取此檔，不使用 `HEAD~N`。

---

### 任務 1：建立隔離 AWS env 與精確 Kiro argv

**文件：**
- 建立：`multi_profile/runtime_env.py`
- 建立：`multi_profile/output.py`
- 建立：`multi_profile/process_utils.py`
- 建立：`tests/test_multi_profile_runtime_env.py`
- 建立：`tests/test_multi_profile_output.py`
- 建立：`tests/test_multi_profile_process_utils.py`
- 修改：`multi_profile/__init__.py`

- [ ] **步驟 1：編寫 child env 與 command 的失敗測試**

建立 `tests/test_multi_profile_runtime_env.py`：

```python
from multi_profile.models import ExecutionContext, ProfileConfig, build_profile_fingerprint
from multi_profile.runtime_env import build_child_env, build_kiro_command


def make_context(**profile_changes):
    values = {
        "profile_id": "prod-cn",
        "aws_profile": "production",
        "aws_region": "cn-northwest-1",
        "expected_account_id": "123456789012",
        "kiro_agent": "my-dev-bot",
        "model": "claude-sonnet",
        "working_dir": "/srv/kiro-devops",
    }
    values.update(profile_changes)
    profile = ProfileConfig(**values)
    return ExecutionContext(
        config_generation=2,
        platform="feishu",
        app_key="ops-bot",
        chat_type="group",
        chat_id="oc_prod",
        user_id="ou_user",
        principal_key="feishu/ops-bot/group/oc_prod/user/ou_user",
        group_scope_key="feishu/ops-bot/group/oc_prod",
        profile_id=profile.profile_id,
        profile=profile,
        profile_fingerprint=build_profile_fingerprint(profile),
    )


def test_child_env_removes_parent_aws_credentials_without_mutating_input():
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/ubuntu",
        "AWS_ACCESS_KEY_ID": "wrong-key",
        "AWS_SECRET_ACCESS_KEY": "wrong-secret",
        "AWS_SESSION_TOKEN": "wrong-token",
        "AWS_PROFILE": "wrong-profile",
        "AWS_DEFAULT_PROFILE": "wrong-default",
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
    }

    child = build_child_env(make_context(), base)

    assert base["AWS_ACCESS_KEY_ID"] == "wrong-key"
    assert "AWS_ACCESS_KEY_ID" not in child
    assert "AWS_SECRET_ACCESS_KEY" not in child
    assert "AWS_SESSION_TOKEN" not in child
    assert child["AWS_PROFILE"] == "production"
    assert child["AWS_DEFAULT_PROFILE"] == "production"
    assert child["AWS_REGION"] == "cn-northwest-1"
    assert child["AWS_DEFAULT_REGION"] == "cn-northwest-1"
    assert child["AWS_SDK_LOAD_CONFIG"] == "1"
    assert child["NO_COLOR"] == "1"


def test_child_env_omits_region_when_profile_does_not_override_it():
    child = build_child_env(
        make_context(aws_region=None),
        {"AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "us-east-1"},
    )

    assert "AWS_REGION" not in child
    assert "AWS_DEFAULT_REGION" not in child


def test_new_session_command_has_no_resume_flag():
    command = build_kiro_command("/usr/bin/kiro-cli", make_context(), "hello", None)

    assert "--resume" not in command
    assert "--resume-id" not in command
    assert command[-1] == "hello"


def test_existing_session_uses_exact_resume_id():
    command = build_kiro_command(
        "/usr/bin/kiro-cli",
        make_context(),
        "continue",
        "11111111-1111-1111-1111-111111111111",
    )

    index = command.index("--resume-id")
    assert command[index + 1] == "11111111-1111-1111-1111-111111111111"
    assert "--resume" not in command


def test_configured_agent_and_model_are_passed_exactly():
    command = build_kiro_command(
        "/usr/bin/kiro-cli",
        make_context(kiro_agent="my-dev-bot", model="claude-sonnet"),
        "hello",
        None,
    )

    assert command[command.index("--agent") + 1] == "my-dev-bot"
    assert command[command.index("--model") + 1] == "claude-sonnet"


def test_optional_agent_and_model_are_omitted():
    command = build_kiro_command(
        "/usr/bin/kiro-cli",
        make_context(kiro_agent=None, model=None),
        "hello",
        None,
    )

    assert "--agent" not in command
    assert "--model" not in command
```

建立 `tests/test_multi_profile_output.py`：

```python
from multi_profile.output import clean_output


def test_clean_output_removes_ansi_and_kiro_banner():
    stdout = "\x1b[31mAll tools are now trusted\x1b[0m\nanswer\nCredits: 1 Time: 2"

    assert clean_output(stdout, "") == "answer"


def test_clean_output_uses_stderr_and_default_message():
    assert clean_output("", "failure") == "failure"
    assert clean_output("", "") == "Kiro 未返回結果"
```

建立 `tests/test_multi_profile_process_utils.py`：

```python
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
```

- [ ] **步驟 2：執行測試並確認 modules 不存在**

```bash
pytest -q \
  tests/test_multi_profile_runtime_env.py \
  tests/test_multi_profile_output.py \
  tests/test_multi_profile_process_utils.py
```

預期：FAIL，包含 `ModuleNotFoundError`，分別指出 `runtime_env`、`output` 或 `process_utils` 尚不存在。

- [ ] **步驟 3：實作 env 與 command builder**

建立 `multi_profile/runtime_env.py`：

```python
from __future__ import annotations

import os
from collections.abc import Mapping

from .models import ExecutionContext


_AWS_SELECTOR_VARS = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
}


def build_child_env(
    context: ExecutionContext,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    child = dict(os.environ if base_env is None else base_env)
    for name in _AWS_SELECTOR_VARS:
        child.pop(name, None)

    child["AWS_PROFILE"] = context.profile.aws_profile
    child["AWS_DEFAULT_PROFILE"] = context.profile.aws_profile
    if context.profile.aws_region:
        child["AWS_REGION"] = context.profile.aws_region
        child["AWS_DEFAULT_REGION"] = context.profile.aws_region
    child["AWS_SDK_LOAD_CONFIG"] = "1"
    child["NO_COLOR"] = "1"
    return child


def build_kiro_command(
    kiro_bin: str,
    context: ExecutionContext,
    prompt: str,
    session_id: str | None,
) -> list[str]:
    command = [
        kiro_bin,
        "chat",
        "--no-interactive",
        "-a",
        "--trust-tools=execute_bash",
        "--wrap",
        "never",
    ]
    if session_id:
        command += ["--resume-id", session_id]
    if context.profile.kiro_agent:
        command += ["--agent", context.profile.kiro_agent]
    if context.profile.model:
        command += ["--model", context.profile.model]
    command.append(prompt)
    return command
```

建立 `multi_profile/output.py`：

```python
import re


_ANSI_CSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_ANSI_OSC = re.compile(r"\x1b\].*?\x07")
_SKIP_TEXT = (
    "All tools are now trusted",
    "understand the risks",
    "Learn more at",
    "Credits:",
    "/model",
    "/prompts",
    "Did you know",
)


def clean_output(stdout: str, stderr: str) -> str:
    text = stdout.strip() or stderr.strip() or "Kiro 未返回結果"
    text = _ANSI_CSI.sub("", text)
    text = _ANSI_OSC.sub("", text)
    lines = [
        line
        for line in text.splitlines()
        if not any(marker in line.strip() for marker in _SKIP_TEXT)
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
```

建立 `multi_profile/process_utils.py`：

```python
from __future__ import annotations

import os
import signal
from collections.abc import Callable
from typing import Any


def terminate_process_tree(
    process: Any,
    *,
    getpgid: Callable[[int], int] = os.getpgid,
    killpg: Callable[[int, int], None] = os.killpg,
) -> None:
    try:
        killpg(getpgid(process.pid), signal.SIGKILL)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    try:
        process.wait()
    except Exception:
        pass
```

在 `multi_profile/__init__.py` 追加：

```python
from .output import clean_output
from .process_utils import terminate_process_tree
from .runtime_env import build_child_env, build_kiro_command

__all__ += [
    "build_child_env",
    "build_kiro_command",
    "clean_output",
    "terminate_process_tree",
]
```

- [ ] **步驟 4：執行測試**

```bash
pytest -q \
  tests/test_multi_profile_runtime_env.py \
  tests/test_multi_profile_output.py \
  tests/test_multi_profile_process_utils.py
```

預期：10 passed。

- [ ] **步驟 5：提交任務 1**

```bash
git add \
  multi_profile/runtime_env.py \
  multi_profile/output.py \
  multi_profile/process_utils.py \
  multi_profile/__init__.py \
  tests/test_multi_profile_runtime_env.py \
  tests/test_multi_profile_output.py \
  tests/test_multi_profile_process_utils.py
git commit -m "feat(多租戶): 隔離 AWS 執行環境"
```

---

### 任務 2：建立 per-principal TaskRegistry

**文件：**
- 建立：`multi_profile/task_registry.py`
- 建立：`tests/test_multi_profile_task_registry.py`
- 修改：`multi_profile/__init__.py`

- [ ] **步驟 1：編寫保留、並行、取消與 token 安全測試**

建立 `tests/test_multi_profile_task_registry.py`：

```python
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
```

- [ ] **步驟 2：執行測試並確認 module 不存在**

```bash
pytest -q tests/test_multi_profile_task_registry.py
```

預期：FAIL，包含 `ModuleNotFoundError`。

- [ ] **步驟 3：實作 TaskRegistry**

建立 `multi_profile/task_registry.py`：

```python
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable


class TaskAlreadyRunning(RuntimeError):
    pass


@dataclass(frozen=True)
class CancellationHandle:
    token: str
    process: Any


@dataclass
class _RunningTask:
    token: str
    profile_id: str
    started_at: float
    process: Any = None
    cancel_requested: bool = False


class TaskRegistry:
    def __init__(self, *, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._tasks: dict[str, _RunningTask] = {}
        self._lock = threading.Lock()

    def reserve(self, principal_key: str, profile_id: str) -> str:
        with self._lock:
            if principal_key in self._tasks:
                raise TaskAlreadyRunning(principal_key)
            token = uuid.uuid4().hex
            self._tasks[principal_key] = _RunningTask(
                token=token,
                profile_id=profile_id,
                started_at=self._clock(),
            )
            return token

    def attach(self, principal_key: str, token: str, process: Any) -> bool:
        with self._lock:
            task = self._tasks.get(principal_key)
            if task is None or task.token != token:
                raise RuntimeError("task reservation no longer exists")
            task.process = process
            return task.cancel_requested

    def finish(self, principal_key: str, token: str) -> None:
        with self._lock:
            task = self._tasks.get(principal_key)
            if task is not None and task.token == token:
                self._tasks.pop(principal_key, None)

    def claim_completion(self, principal_key: str, token: str) -> bool:
        with self._lock:
            task = self._tasks.get(principal_key)
            if task is None or task.token != token or task.cancel_requested:
                return False
            self._tasks.pop(principal_key, None)
            return True

    def should_cancel(self, principal_key: str, token: str) -> bool:
        with self._lock:
            task = self._tasks.get(principal_key)
            return task is None or task.token != token or task.cancel_requested

    def is_busy(self, principal_key: str) -> bool:
        with self._lock:
            return principal_key in self._tasks

    def status(self, principal_key: str) -> str | None:
        with self._lock:
            task = self._tasks.get(principal_key)
            if task is None:
                return None
            elapsed = max(0, int(self._clock() - task.started_at))
            state = "cancelling" if task.cancel_requested else "running"
            return f"{task.profile_id} task {state} ({elapsed}s)"

    def request_cancel(self, principal_key: str) -> CancellationHandle | None:
        with self._lock:
            task = self._tasks.get(principal_key)
            if task is None:
                return None
            task.cancel_requested = True
            return CancellationHandle(task.token, task.process)
```

在 `multi_profile/__init__.py` 追加：

```python
from .task_registry import CancellationHandle, TaskAlreadyRunning, TaskRegistry

__all__ += ["CancellationHandle", "TaskAlreadyRunning", "TaskRegistry"]
```

- [ ] **步驟 4：執行測試**

```bash
pytest -q tests/test_multi_profile_task_registry.py
```

預期：9 passed。

- [ ] **步驟 5：提交任務 2**

```bash
git add multi_profile/task_registry.py multi_profile/__init__.py tests/test_multi_profile_task_registry.py
git commit -m "feat(多租戶): 隔離 principal 執行中任務"
```

---

### 任務 3：建立 SQLite SessionStore

**文件：**
- 建立：`multi_profile/session_store.py`
- 建立：`tests/test_multi_profile_session_store.py`
- 修改：`multi_profile/__init__.py`

- [ ] **步驟 1：編寫註冊、解析、fingerprint、恢復與裁剪測試**

建立 `tests/test_multi_profile_session_store.py`：

```python
import pytest

from multi_profile.models import ExecutionContext, ProfileConfig, build_profile_fingerprint
from multi_profile.session_store import SessionStore


def make_context(principal="principal-a", **profile_changes):
    values = {
        "profile_id": "prod-cn",
        "aws_profile": "production",
        "aws_region": "cn-northwest-1",
        "expected_account_id": "123456789012",
        "working_dir": "/srv/kiro-devops",
        "kiro_agent": "agent-a",
        "model": "model-a",
    }
    values.update(profile_changes)
    profile = ProfileConfig(**values)
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


def test_register_and_resolve_latest_session(tmp_path):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    context = make_context()

    record = store.register_new(context, "session-1", "first topic", now=100.0)

    assert record.short_id == 1
    assert store.resolve_active(context, now=200.0, timeout=1800).kiro_session_id == "session-1"


def test_different_principals_are_isolated(tmp_path):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    first = make_context("principal-a")
    second = make_context("principal-b")
    store.register_new(first, "session-a", "A", now=100.0)

    assert store.resolve_active(second, now=200.0, timeout=1800) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"profile_id": "other-profile"},
        {"aws_profile": "other-aws"},
        {"aws_region": "cn-north-1"},
        {"kiro_agent": "agent-b"},
        {"model": "model-b"},
        {"working_dir": "/srv/other"},
    ],
)
def test_latest_fingerprint_mismatch_forces_new_session(tmp_path, changes):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    original = make_context()
    changed = make_context(**changes)
    store.register_new(original, "session-a", "A", now=100.0)

    assert store.resolve_active(changed, now=200.0, timeout=1800) is None


def test_timeout_changes_do_not_invalidate_session(tmp_path):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    original = make_context()
    changed = make_context(sync_timeout=240, async_timeout=2400, alert_timeout=600)
    store.register_new(original, "session-a", "A", now=100.0)

    assert store.resolve_active(changed, now=200.0, timeout=1800).kiro_session_id == "session-a"


def test_expired_latest_session_does_not_fall_back_to_older_one(tmp_path):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    context = make_context()
    store.register_new(context, "session-old", "old", now=10.0)
    store.register_new(context, "session-latest", "latest", now=20.0)

    assert store.resolve_active(context, now=2000.0, timeout=1800) is None


def test_clear_active_expires_all_sessions_for_principal(tmp_path):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    context = make_context()
    store.register_new(context, "session-a", "A", now=100.0)
    store.clear_active(context.principal_key)

    assert store.resolve_active(context, now=101.0, timeout=1800) is None


def test_short_id_resume_requires_matching_fingerprint(tmp_path):
    store = SessionStore(tmp_path / "tenant_sessions.db")
    original = make_context(model="model-a")
    changed = make_context(model="model-b")
    record = store.register_new(original, "session-a", "A", now=100.0)

    assert store.get_by_short_id(original, record.short_id) is not None
    assert store.get_by_short_id(changed, record.short_id) is None


def test_keeps_only_latest_twenty_sessions_per_principal(tmp_path):
    store = SessionStore(tmp_path / "tenant_sessions.db", max_sessions_per_principal=20)
    context = make_context()
    for index in range(25):
        store.register_new(context, f"session-{index}", str(index), now=float(index + 1))

    records = store.list_sessions(context, limit=100)

    assert len(records) == 20
    assert records[0].kiro_session_id == "session-24"
    assert records[-1].kiro_session_id == "session-5"
```

- [ ] **步驟 2：執行測試並確認 module 不存在**

```bash
pytest -q tests/test_multi_profile_session_store.py
```

預期：FAIL，包含 `ModuleNotFoundError`。

- [ ] **步驟 3：實作 SessionStore 與 schema**

建立 `multi_profile/session_store.py`：

```python
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .models import ExecutionContext


@dataclass(frozen=True)
class SessionRecord:
    principal_key: str
    kiro_session_id: str
    profile_id: str
    profile_fingerprint: str
    short_id: int
    topic: str
    created_at: float
    last_active: float
    message_count: int


class SessionStore:
    def __init__(self, db_path: str | Path, *, max_sessions_per_principal: int = 20):
        self.db_path = str(db_path)
        self.max_sessions_per_principal = max_sessions_per_principal
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tenant_sessions (
                    principal_key TEXT NOT NULL,
                    kiro_session_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    profile_fingerprint TEXT NOT NULL,
                    short_id INTEGER NOT NULL,
                    topic TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_active REAL NOT NULL,
                    message_count INTEGER NOT NULL,
                    PRIMARY KEY (principal_key, kiro_session_id),
                    UNIQUE (principal_key, short_id)
                );
                CREATE INDEX IF NOT EXISTS idx_tenant_sessions_active
                    ON tenant_sessions(principal_key, last_active DESC);
                """
            )

    @staticmethod
    def _record(row: sqlite3.Row | None) -> SessionRecord | None:
        return SessionRecord(**dict(row)) if row is not None else None

    def register_new(
        self,
        context: ExecutionContext,
        session_id: str,
        topic: str,
        *,
        now: float | None = None,
    ) -> SessionRecord:
        timestamp = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(short_id), 0) + 1 AS next_id "
                "FROM tenant_sessions WHERE principal_key = ?",
                (context.principal_key,),
            ).fetchone()
            short_id = int(row["next_id"])
            conn.execute(
                """
                INSERT INTO tenant_sessions (
                    principal_key, kiro_session_id, profile_id,
                    profile_fingerprint, short_id, topic,
                    created_at, last_active, message_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    context.principal_key,
                    session_id,
                    context.profile_id,
                    context.profile_fingerprint,
                    short_id,
                    topic[:30],
                    timestamp,
                    timestamp,
                ),
            )
            conn.execute(
                """
                DELETE FROM tenant_sessions
                WHERE principal_key = ? AND kiro_session_id NOT IN (
                    SELECT kiro_session_id FROM tenant_sessions
                    WHERE principal_key = ?
                    ORDER BY short_id DESC LIMIT ?
                )
                """,
                (
                    context.principal_key,
                    context.principal_key,
                    self.max_sessions_per_principal,
                ),
            )
            conn.commit()
        return self.get_by_short_id(context, short_id)

    def resolve_active(
        self,
        context: ExecutionContext,
        *,
        now: float | None = None,
        timeout: int = 1800,
    ) -> SessionRecord | None:
        timestamp = time.time() if now is None else now
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM tenant_sessions
                WHERE principal_key = ?
                ORDER BY last_active DESC, short_id DESC LIMIT 1
                """,
                (context.principal_key,),
            ).fetchone()
        record = self._record(row)
        if record is None:
            return None
        if record.last_active <= 0 or timestamp - record.last_active >= timeout:
            return None
        if record.profile_fingerprint != context.profile_fingerprint:
            return None
        return record

    def touch(
        self,
        context: ExecutionContext,
        session_id: str,
        *,
        now: float | None = None,
    ) -> None:
        timestamp = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tenant_sessions
                SET last_active = ?, message_count = message_count + 1
                WHERE principal_key = ? AND kiro_session_id = ?
                  AND profile_fingerprint = ?
                """,
                (
                    timestamp,
                    context.principal_key,
                    session_id,
                    context.profile_fingerprint,
                ),
            )
            conn.commit()

    def clear_active(self, principal_key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tenant_sessions SET last_active = 0 WHERE principal_key = ?",
                (principal_key,),
            )
            conn.commit()

    def get_by_short_id(
        self,
        context: ExecutionContext,
        short_id: int,
    ) -> SessionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM tenant_sessions
                WHERE principal_key = ? AND short_id = ?
                  AND profile_fingerprint = ?
                """,
                (context.principal_key, short_id, context.profile_fingerprint),
            ).fetchone()
        return self._record(row)

    def list_sessions(
        self,
        context: ExecutionContext,
        *,
        limit: int = 10,
    ) -> list[SessionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tenant_sessions
                WHERE principal_key = ? AND profile_fingerprint = ?
                ORDER BY last_active DESC, short_id DESC LIMIT ?
                """,
                (context.principal_key, context.profile_fingerprint, limit),
            ).fetchall()
        return [self._record(row) for row in rows]
```

在 `multi_profile/__init__.py` 追加：

```python
from .session_store import SessionRecord, SessionStore

__all__ += ["SessionRecord", "SessionStore"]
```

- [ ] **步驟 4：執行 SessionStore 測試**

```bash
pytest -q tests/test_multi_profile_session_store.py
```

預期：13 passed。

- [ ] **步驟 5：提交任務 3**

```bash
git add multi_profile/session_store.py multi_profile/__init__.py tests/test_multi_profile_session_store.py
git commit -m "feat(多租戶): 建立隔離 SessionStore"
```

---

### 任務 4：建立並行安全 SessionCaptureCoordinator

**文件：**
- 建立：`multi_profile/session_capture.py`
- 建立：`tests/test_multi_profile_session_capture.py`
- 修改：`multi_profile/__init__.py`

- [ ] **步驟 1：編寫解析、成功、歧義、timeout 與 canonical lock 測試**

建立 `tests/test_multi_profile_session_capture.py`：

```python
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
```

- [ ] **步驟 2：執行測試並確認 module 不存在**

```bash
pytest -q tests/test_multi_profile_session_capture.py
```

預期：FAIL，包含 `ModuleNotFoundError`。

- [ ] **步驟 3：實作 Coordinator**

建立 `multi_profile/session_capture.py`：

```python
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .process_utils import terminate_process_tree


_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


class SessionCaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapturedSession:
    session_id: str
    process: Any


def parse_session_ids(text: str) -> set[str]:
    return {match.lower() for match in _UUID_RE.findall(text)}


class SessionCaptureCoordinator:
    def __init__(
        self,
        *,
        timeout: float = 30,
        poll_interval: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        terminate_process: Callable[[Any], None] = terminate_process_tree,
    ):
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._clock = clock
        self._sleep = sleep
        self._terminate_process = terminate_process
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, working_dir: str | Path) -> threading.Lock:
        key = str(Path(working_dir).resolve())
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def start_and_capture(
        self,
        working_dir: str | Path,
        *,
        list_session_ids: Callable[[], set[str]],
        start_process: Callable[[], Any],
    ) -> CapturedSession:
        with self._lock_for(working_dir):
            before = set(list_session_ids())
            process = start_process()
            deadline = self._clock() + self._timeout
            try:
                while True:
                    current = set(list_session_ids())
                    new_ids = current - before
                    if len(new_ids) == 1:
                        return CapturedSession(new_ids.pop(), process)
                    if len(new_ids) > 1:
                        raise SessionCaptureError("ambiguous new Kiro sessions")
                    if self._clock() >= deadline:
                        raise SessionCaptureError("Kiro session capture timed out")
                    self._sleep(self._poll_interval)
            except SessionCaptureError:
                self._terminate_process(process)
                raise
            except Exception as exc:
                self._terminate_process(process)
                raise SessionCaptureError("Kiro session listing failed") from exc
```

在 `multi_profile/__init__.py` 追加：

```python
from .session_capture import (
    CapturedSession,
    SessionCaptureCoordinator,
    SessionCaptureError,
    parse_session_ids,
)

__all__ += [
    "CapturedSession",
    "SessionCaptureCoordinator",
    "SessionCaptureError",
    "parse_session_ids",
]
```

- [ ] **步驟 4：執行 Coordinator 測試**

```bash
pytest -q tests/test_multi_profile_session_capture.py
```

預期：9 passed。

- [ ] **步驟 5：提交任務 4**

```bash
git add multi_profile/session_capture.py multi_profile/__init__.py tests/test_multi_profile_session_capture.py
git commit -m "feat(多租戶): 安全捕捉 Kiro Session ID"
```

---

### 任務 5：建立 ContextRuntime 同步執行與 Session 整合

**文件：**
- 建立：`multi_profile/runtime.py`
- 建立：`tests/test_multi_profile_runtime.py`
- 修改：`multi_profile/__init__.py`

- [ ] **步驟 1：編寫 fake process 與同步執行測試**

建立 `tests/test_multi_profile_runtime.py`：

```python
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
```

- [ ] **步驟 2：執行測試並確認 runtime 不存在**

```bash
pytest -q tests/test_multi_profile_runtime.py
```

預期：FAIL，包含 `ModuleNotFoundError`。

- [ ] **步驟 3：實作同步 ContextRuntime**

建立 `multi_profile/runtime.py`：

```python
from __future__ import annotations

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
from .session_capture import SessionCaptureCoordinator, SessionCaptureError, parse_session_ids
from .session_store import SessionStore
from .task_registry import TaskRegistry


@dataclass(frozen=True)
class RuntimeFailure:
    code: str
    message: str
    returncode: int | None = None


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
            return process, existing.kiro_session_id, False

        command = build_kiro_command(self.kiro_bin, context, prompt, None)
        captured = self.session_capture.start_and_capture(
            context.profile.working_dir,
            list_session_ids=lambda: self._list_session_ids(context, env),
            start_process=lambda: self._start_process(command, context, env, token),
        )
        if self.task_registry.should_cancel(context.principal_key, token):
            self._terminate_process(captured.process)
            self.task_registry.finish(context.principal_key, token)
            raise RuntimeCancelled("task cancelled during session capture")
        try:
            self.session_store.register_new(
                context,
                captured.session_id,
                prompt[:30],
                now=self._clock(),
            )
        except Exception:
            self._terminate_process(captured.process)
            raise
        return captured.process, captured.session_id, True

    @staticmethod
    def _output(stdout: str, stderr: str) -> str:
        return clean_output(stdout, stderr)

    def _finish_result(
        self,
        context: ExecutionContext,
        token: str,
        process,
        session_id: str,
        is_new_session: bool,
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
            on_error(RuntimeFailure("process_failed", output, process.returncode))
            return
        if not is_new_session:
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
        try:
            env = build_child_env(context)
            process, session_id, is_new_session = self._start_or_resume(
                context,
                prompt,
                env,
                token,
            )
            try:
                stdout, stderr = process.communicate(timeout=context.profile.sync_timeout)
            except subprocess.TimeoutExpired:
                self._terminate_process(process)
                if self.task_registry.claim_completion(context.principal_key, token):
                    on_error(
                        RuntimeFailure(
                            "sync_timeout",
                            f"任務超過同步等待時間（{context.profile.sync_timeout}s）",
                        )
                    )
                return
        except RuntimeCancelled:
            self.task_registry.finish(context.principal_key, token)
            return
        except Exception as exc:
            if process is not None:
                self._terminate_process(process)
            self.task_registry.finish(context.principal_key, token)
            code = "execution_failed" if process is not None else "startup_failed"
            on_error(RuntimeFailure(code, str(exc)))
            return

        self._finish_result(
            context,
            token,
            process,
            session_id,
            is_new_session,
            stdout,
            stderr,
            on_result=on_sync_result,
            on_error=on_error,
        )
```

在 `multi_profile/__init__.py` 追加：

```python
from .runtime import ContextRuntime, RuntimeFailure

__all__ += ["ContextRuntime", "RuntimeFailure"]
```

- [ ] **步驟 4：執行同步 runtime 測試**

```bash
pytest -q tests/test_multi_profile_runtime.py
```

預期：8 passed。

- [ ] **步驟 5：提交任務 5**

```bash
git add multi_profile/runtime.py multi_profile/__init__.py tests/test_multi_profile_runtime.py
git commit -m "feat(多租戶): 建立 Context Runtime 同步路徑"
```

---

### 任務 6：完成異步、進度、取消與程序樹終止

**文件：**
- 修改：`multi_profile/runtime.py`
- 修改：`tests/test_multi_profile_runtime.py`

- [ ] **步驟 1：追加異步與取消紅燈測試**

在 `tests/test_multi_profile_runtime.py` 追加：

```python
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
```

- [ ] **步驟 2：執行新增測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_runtime.py
```

預期：新增案例 FAIL，因同步 timeout 尚未轉入 async worker，且 `cancel()`、completion/cancel ownership 與 async error cleanup 尚未完成。

- [ ] **步驟 3：在 `ContextRuntime` 加入取消 API**

在 `ContextRuntime` 類別中加入：

```python
    def cancel(self, context: ExecutionContext) -> bool:
        handle = self.task_registry.request_cancel(context.principal_key)
        if handle is None:
            return False
        if handle.process is not None:
            self._terminate_process(handle.process)
            self.task_registry.finish(context.principal_key, handle.token)
        return True
```

若取消發生在 Popen 前，reservation 保留 `cancel_requested=True`；`_start_process()` attach 後立即終止。若取消發生在 Session 捕捉期間，`_start_or_resume()` 在註冊 Session 前再次檢查 `should_cancel()`。

- [ ] **步驟 4：加入異步 wait helper**

在 `ContextRuntime` 類別中加入：

```python
    def _wait_async(
        self,
        context: ExecutionContext,
        token: str,
        process,
        session_id: str,
        is_new_session: bool,
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
                    is_new_session,
                    stdout,
                    stderr,
                    on_result=on_async_result,
                    on_error=on_error,
                )
                return

            self._terminate_process(process)
            if self.task_registry.claim_completion(context.principal_key, token):
                on_error(
                    RuntimeFailure(
                        "async_timeout",
                        f"任務超時（{context.profile.async_timeout}s），已終止",
                    )
                )
        except Exception as exc:
            self._terminate_process(process)
            if self.task_registry.claim_completion(context.principal_key, token):
                on_error(RuntimeFailure("async_failed", str(exc)))
        finally:
            self.task_registry.finish(context.principal_key, token)
```

- [ ] **步驟 5：以異步轉換取代同步 timeout 分支**

在 `execute()` 中將 `except subprocess.TimeoutExpired:` 區塊完整替換為：

```python
            except subprocess.TimeoutExpired:
                try:
                    on_async_start()
                    worker = self._thread_factory(
                        target=lambda: self._wait_async(
                            context,
                            token,
                            process,
                            session_id,
                            is_new_session,
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
```

- [ ] **步驟 6：執行 runtime 全測試**

```bash
pytest -q tests/test_multi_profile_runtime.py tests/test_multi_profile_task_registry.py
```

預期：18 個 runtime 案例與 9 個 registry 案例全部 PASS。

- [ ] **步驟 7：提交任務 6**

```bash
git add multi_profile/runtime.py tests/test_multi_profile_runtime.py
git commit -m "feat(多租戶): 完成 Context Runtime 異步與取消"
```

---

### 任務 7：建立 Memory／Event scope 邊界並驗證跨 App、跨群隔離

**文件：**
- 建立：`multi_profile/scoped_state.py`
- 建立：`tests/test_multi_profile_memory_isolation.py`
- 修改：`multi_profile/__init__.py`

- [ ] **步驟 1：先寫 scope 與 scoped event ID 紅燈測試**

建立 `tests/test_multi_profile_memory_isolation.py`：

```python
from event_store import EventStore
from memory import MemoryLayer
from multi_profile.models import AppConfig, ProfileConfig, RouteConfig, create_snapshot
from multi_profile.router import TenantRouter
from multi_profile.scoped_state import event_owner, scoped_event_id, semantic_owner


def build_contexts(tmp_path):
    apps = {
        key: AppConfig(
            app_key=key,
            app_id_env=f"{key.upper().replace('-', '_')}_ID",
            app_secret_env=f"{key.upper().replace('-', '_')}_SECRET",
            default_profile="prod-cn",
        )
        for key in ("app-a", "app-b")
    }
    profile = ProfileConfig(
        profile_id="prod-cn",
        aws_profile="production",
        expected_account_id="123456789012",
        working_dir=str(tmp_path),
    )
    snapshot = create_snapshot(
        1,
        apps,
        {"prod-cn": profile},
        (
            RouteConfig("app-a", "oc_shared", "prod-cn"),
            RouteConfig("app-b", "oc_shared", "prod-cn"),
            RouteConfig("app-a", "oc_other", "prod-cn"),
        ),
    )
    router = TenantRouter(snapshot)
    common = {
        "platform": "feishu",
        "chat_type": "group",
        "user_id": "ou_same_user",
    }
    return (
        router.resolve(app_key="app-a", chat_id="oc_shared", **common),
        router.resolve(app_key="app-b", chat_id="oc_shared", **common),
        router.resolve(app_key="app-a", chat_id="oc_other", **common),
    )


def test_scope_owners_come_from_router_context(tmp_path):
    app_a, app_b, other_group = build_contexts(tmp_path)

    assert semantic_owner(app_a) != semantic_owner(app_b)
    assert semantic_owner(app_a) != semantic_owner(other_group)
    assert event_owner(app_a) != event_owner(app_b)
    assert event_owner(app_a) != event_owner(other_group)


def test_same_external_event_id_is_scoped_per_group(tmp_path):
    app_a, app_b, other_group = build_contexts(tmp_path)

    first = scoped_event_id(app_a, "prometheus-alert-1")
    second = scoped_event_id(app_b, "prometheus-alert-1")
    third = scoped_event_id(other_group, "prometheus-alert-1")

    assert len({first, second, third}) == 3
    assert first == scoped_event_id(app_a, "prometheus-alert-1")


def test_semantic_memory_characterization_uses_principal_scope(tmp_path):
    memory = MemoryLayer(db_path=str(tmp_path / "memory"))
    app_a, app_b, other_group = build_contexts(tmp_path)
    memory.add(semantic_owner(app_a), "app A group secret")

    assert memory.list_all(semantic_owner(app_a)) == ["app A group secret"]
    assert memory.list_all(semantic_owner(app_b)) == []
    assert memory.list_all(semantic_owner(other_group)) == []


def test_event_store_characterization_preserves_same_external_id_per_scope(tmp_path):
    events = EventStore(tmp_path / "events.db")
    app_a, app_b, _ = build_contexts(tmp_path)
    external_id = "prometheus-alert-1"

    events.add_event(
        user_id=event_owner(app_a),
        event_id=scoped_event_id(app_a, external_id),
        title="app A deployment",
        event_type="应用发版",
    )
    events.add_event(
        user_id=event_owner(app_b),
        event_id=scoped_event_id(app_b, external_id),
        title="app B deployment",
        event_type="应用发版",
    )

    assert len(events.search_events(event_owner(app_a), query="deployment")) == 1
    assert len(events.search_events(event_owner(app_b), query="deployment")) == 1
```

- [ ] **步驟 2：執行測試並確認 scoped_state 不存在**

```bash
pytest -q tests/test_multi_profile_memory_isolation.py
```

預期：FAIL，包含 `ModuleNotFoundError: No module named 'multi_profile.scoped_state'`。後兩個 Store 測試是既有 SQLite 行為的 characterization；前兩個測試才是新 helper 的 TDD 紅燈。

- [ ] **步驟 3：實作 scope helper 與 deterministic scoped event ID**

建立 `multi_profile/scoped_state.py`：

```python
import hashlib

from .models import ExecutionContext


def semantic_owner(context: ExecutionContext) -> str:
    return context.principal_key


def event_owner(context: ExecutionContext) -> str:
    return context.group_scope_key or context.principal_key


def scoped_event_id(context: ExecutionContext, external_id: str) -> str:
    if not external_id or not external_id.strip():
        raise ValueError("external_id must not be empty")
    owner = event_owner(context)
    payload = f"{owner}\0{external_id.strip()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

在 `multi_profile/__init__.py` 追加：

```python
from .scoped_state import event_owner, scoped_event_id, semantic_owner

__all__ += ["event_owner", "scoped_event_id", "semantic_owner"]
```

- [ ] **步驟 4：執行真實 SQLite 隔離測試**

```bash
pytest -q tests/test_multi_profile_memory_isolation.py
```

預期：4 passed；同 user／chat ID 跨 App、同 App 跨群都不共享 Semantic Memory 或 Event identity。

- [ ] **步驟 5：提交任務 7**

```bash
git add \
  multi_profile/scoped_state.py \
  multi_profile/__init__.py \
  tests/test_multi_profile_memory_isolation.py
git commit -m "feat(多租戶): 隔離記憶與事件 scope"
```

---

### 任務 8：公開介面與計畫級完整驗證

**文件：**
- 修改：`multi_profile/__init__.py`（只在前面任務遺漏匯出時修正）

- [ ] **步驟 1：確認計畫 3 所需公開介面可匯入**

```bash
python3 - <<'PY'
from multi_profile import (
    CancellationHandle,
    CapturedSession,
    ContextRuntime,
    ExecutionContext,
    RuntimeFailure,
    SessionCaptureCoordinator,
    SessionCaptureError,
    SessionRecord,
    SessionStore,
    TaskAlreadyRunning,
    TaskRegistry,
    build_child_env,
    build_kiro_command,
    clean_output,
    event_owner,
    parse_session_ids,
    scoped_event_id,
    semantic_owner,
    terminate_process_tree,
)
print("plan 2 public API import OK")
PY
```

預期：輸出 `plan 2 public API import OK`。

- [ ] **步驟 2：執行計畫 2 targeted tests**

```bash
pytest -q \
  tests/test_multi_profile_runtime_env.py \
  tests/test_multi_profile_output.py \
  tests/test_multi_profile_process_utils.py \
  tests/test_multi_profile_task_registry.py \
  tests/test_multi_profile_session_store.py \
  tests/test_multi_profile_session_capture.py \
  tests/test_multi_profile_runtime.py \
  tests/test_multi_profile_memory_isolation.py
```

預期：全部 PASS，0 failed。

- [ ] **步驟 3：執行計畫 1 + 計畫 2 全部測試**

```bash
pytest -q tests/test_multi_profile_*.py
```

預期：全部 PASS。

- [ ] **步驟 4：執行 legacy Runtime、Session、Memory 回歸**

```bash
pytest -q \
  test_memory.py \
  test_step3_integration.py \
  test_step5_integration.py \
  tests/test_group_alert_detection.py \
  tests/test_platform_dispatcher.py
```

預期：全部 PASS，舊 `KiroExecutor`／`SessionRouter`／Memory 呼叫方式未被修改。

- [ ] **步驟 5：執行完整測試套件**

```bash
pytest -q
```

預期：0 failed。若出現既有基線失敗，停止並依 systematic-debugging 確認，不得忽略。

- [ ] **步驟 6：執行 Python 編譯檢查**

```bash
python3 -m compileall -q multi_profile tests
```

預期：exit 0。

- [ ] **步驟 7：確認未修改 legacy 生產路徑**

```bash
PLAN2_BASE_SHA=$(cat .git/plan2-base-sha)
git diff "${PLAN2_BASE_SHA}"..HEAD -- \
  gateway.py message_handler.py kiro_executor.py session_router.py \
  memory.py semantic_store.py event_store.py alert_analysis.py \
  platform_dispatcher.py adapters dashboard
```

預期：沒有輸出。

- [ ] **步驟 8：確認提交與工作區**

```bash
git status --short
PLAN2_BASE_SHA=$(cat .git/plan2-base-sha)
git log --oneline "${PLAN2_BASE_SHA}"..HEAD
```

預期：沒有未提交的計畫 2 檔案；列出的範圍只包含計畫 2 實作與其審查修正提交。

---

## 完成標準

- 每個 Kiro child env 都移除父程序 AWS credential selectors，再注入 ExecutionContext 的 AWS profile／Region。
- 任何恢復命令只使用 `--resume-id <UUID>`，不含 `--resume`。
- 同 principal 在程序啟動前即被 reserve；不同 principal 可並行。
- SessionStore 使用獨立 SQLite，按 principal 與 fingerprint 限制解析／恢復。
- 新 Session UUID 只在「恰好一個新增 ID」時綁定；歧義或 timeout 會終止程序並 fail-closed。
- Runtime 完成同步、轉異步、進度、取消及最終 timeout；不在 status／progress 中保存或輸出 prompt。
- 真實 SemanticStore／EventStore 測試證明同使用者跨群不共享資料。
- 未修改或接線 legacy gateway、executor、session、memory、alert、adapter 或 Dashboard。
- Targeted、計畫 1+2、legacy、完整 pytest 與 compileall 全部通過。

## 不在本計畫範圍

- 不修改 `IncomingMessage` 或加入 `app_key`。
- 不建立 AppManager 或多 FeishuAdapter。
- 不修改 PlatformDispatcher registry key。
- 不把 ContextRuntime 接到 MessageHandler。
- 不改 `/new`、`/resume`、`/sessions`、`/status` 或 `/cancel` 命令；計畫 3 接線後才切換。
- 不把群告警接到 ExecutionContext。
- 不執行 STS 健康檢查或 Dashboard 驗證。
- 不匯入舊 `user_sessions.json` 或舊記憶。
- 不啟用 `MULTI_PROFILE_ENABLED=true`。

## 計畫 3 可依賴的公開介面

```python
from multi_profile import (
    CancellationHandle,
    CapturedSession,
    ContextRuntime,
    ExecutionContext,
    RuntimeFailure,
    SessionCaptureCoordinator,
    SessionCaptureError,
    SessionRecord,
    SessionStore,
    TaskAlreadyRunning,
    TaskRegistry,
    build_child_env,
    build_kiro_command,
    clean_output,
    event_owner,
    parse_session_ids,
    scoped_event_id,
    semantic_owner,
    terminate_process_tree,
)
```

計畫 3 必須將 `ExecutionContext` 傳入 `ContextRuntime`，並使用 `SessionStore` 處理命令；不得讓 multi-profile 路徑回退舊 `user_sessions.json` 或全域 `KiroExecutor`。
