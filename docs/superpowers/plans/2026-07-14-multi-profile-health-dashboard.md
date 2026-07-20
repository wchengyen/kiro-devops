# Profile 健康與 Dashboard 實作計畫

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推薦）或 superpowers:executing-plans 逐任務實現此計畫。步驟使用復選框（`- [ ]`）語法追蹤進度。

**目標：** 在計畫 1–3 已就緒的多 profile 核心上，建立週期性 STS Profile 健康監控（含 jitter、grace period 與 Account ID 立即阻擋）、Dashboard「Multi Profile Config」頁面、伺服器端重新驗證的 Draft 驗證／發布 API、原子發布與 revision 保留、熱載入與 pending-restart 分類、設定回滾，以及符合規格 §17 的可觀測性端點。全程不得保存或輸出任何 Secret 值。

**架構：** 健康檢查、驗證管線、發布器與 revision 儲存全部放在 `multi_profile` package，透過依賴注入隔離 subprocess、clock、sleeper、jitter 與 thread，測試不觸碰真實 AWS。Dashboard 新增獨立 `dashboard/multi_profile_api.py` 模組與一個前端頁面，沿用既有 `require_auth` Cookie Session；執行期依賴（registry、health monitor、publisher、AppManager、TaskRegistry）由 gateway 以 `init_multi_profile_api()` 注入，測試注入 fake。`MULTI_PROFILE_ENABLED=false` 時 API 仍可驗證與保存 Draft（供計畫 5 離線設定使用），但不切換 runtime generation。

**技術棧：** Python 3.10+、標準庫 `subprocess`／`threading`／`hashlib`／`difflib`／`configparser`／`json`、PyYAML、Flask（既有 dashboard blueprint）、pytest。

**依賴：** 必須先完整實作並驗證：
- `docs/superpowers/plans/2026-07-14-multi-profile-routing-core.md`（計畫 1：`load_config`、`ConfigRegistry`、`ConfigSnapshot`、feature flags）。
- `docs/superpowers/plans/2026-07-14-multi-profile-runtime-session-isolation.md`（計畫 2：`build_child_env`、`TaskRegistry`、`ContextRuntime`）。
- 計畫 3（多 App 與群告警整合）：`AppManager` 已建立並暴露每 App 連線狀態；gateway 已存在 multi-profile 訊息入口。本計畫以其公開介面為整合點，若計畫 3 實際匯出名稱與本文假設不同，以計畫 3 為準調整 import，不得改變本計畫的狀態語意。

**參考規格：** `docs/superpowers/specs/2026-07-14-multi-profile-multi-feishu-group-design.md` 第 13、14、16、17、20.1、21.3、22 節。

---

## 檔案結構

### 建立

- `multi_profile/sts.py`：以隔離子環境執行 `aws sts get-caller-identity`；回傳結構化結果與錯誤分類；Account ID 遮罩。
- `multi_profile/operational_settings.py`：解析並驗證 `AWS_STS_TIMEOUT_SEC`、`PROFILE_HEALTH_CHECK_INTERVAL_SEC`、`PROFILE_HEALTH_GRACE_SEC` 與 revision 保留數。
- `multi_profile/health.py`：`ProfileHealthMonitor` 狀態機（active／degraded／blocked／disabled）、jitter 排程、grace period、`ensure_usable()` 任務閘門。
- `multi_profile/revisions.py`：`RevisionStore`（revision YAML＋metadata、checksum、diff、保留 20 份、last-known-good）與 `atomic_write()`。
- `multi_profile/external_validation.py`：規格 §13.3 步驟 5–8 的外部驗證（Kiro Agent／模型、AWS CLI profile、隔離 STS、Account ID 核對）。
- `multi_profile/publisher.py`：`ConfigPublisher` 原子發布、失敗自動回復、熱載入／pending-restart 分類、回滾發布。
- `multi_profile/status.py`：規格 §17 可觀測性聚合。
- `dashboard/multi_profile_api.py`：Multi Profile Dashboard API（全部 `require_auth`）。
- `tests/test_multi_profile_sts.py`
- `tests/test_multi_profile_operational_settings.py`
- `tests/test_multi_profile_health.py`
- `tests/test_multi_profile_revisions.py`
- `tests/test_multi_profile_external_validation.py`
- `tests/test_multi_profile_publisher.py`
- `tests/test_multi_profile_status.py`
- `tests/test_dashboard_api_multi_profile.py`
- `tests/test_multi_profile_log_hygiene.py`

### 修改

- `multi_profile/runtime_env.py`：抽出 `build_profile_env(profile, base_env)`，`build_child_env` 改為委派（additive，計畫 2 既有測試必須保持通過）。
- `multi_profile/task_registry.py`：新增 `counts_by_profile()` 與 `total_running()`（additive）。
- `multi_profile/__init__.py`：匯出計畫 5 與 Dashboard 可依賴的穩定介面。
- `dashboard/__init__.py`：註冊 `dashboard.multi_profile_api`（比照既有 `import dashboard.api`）。
- `dashboard/static/app.js`：新增 `MultiProfilePage` 元件、`/multi-profile` 路由與導航項目。
- `gateway.py`：在計畫 3 建立的 multi-profile 分支啟動健康監控、注入 Dashboard 依賴、在訊息與告警入口加入 blocked 拒絕。
- `.env.example`：新增健康檢查與 revision 相關設定（含界限註解）。
- `.gitignore`：確認 `runtime/` 已忽略（計畫 1 已加入；本計畫只驗證不重複）。

### 明確不修改

- `message_handler.py` 的 legacy 路徑行為（`MULTI_PROFILE_ENABLED=false` 時完全不受影響）。
- `kiro_executor.py`、`session_router.py`、`alert_analysis.py` 的 legacy 流程。
- `adapters/`、`platform_dispatcher.py`（計畫 3 已完成多 App 改造，本計畫不動）。
- `dashboard/api.py`、`dashboard/config_store.py`（既有路由與 .env 編輯行為不變）。
- `multi_profile/models.py`、`multi_profile/config_loader.py`、`multi_profile/router.py`（計畫 1 介面凍結；驗證管線只能組合呼叫，不得改寫）。

---

## 執行前基線

- [ ] **記錄計畫 4 起始 SHA**

```bash
git rev-parse HEAD > .git/plan4-base-sha
cat .git/plan4-base-sha
```

預期：輸出計畫 3 完成後的 HEAD SHA。後續所有範圍驗證都讀取此檔，不使用 `HEAD~N`。

- [ ] **確認計畫 1–3 介面存在**

```bash
python3 - <<'PY'
from multi_profile import (
    ConfigError, ConfigRegistry, ExecutionContext, TaskRegistry,
    TenantRouter, build_child_env, config_path, is_enabled, load_config,
)
print("plan 1-2 API OK")
PY
pytest -q tests/test_multi_profile_models.py tests/test_multi_profile_registry.py tests/test_multi_profile_task_registry.py
```

預期：import 成功且測試全綠；若失敗，停止並先修復基線。

- [ ] **確認計畫 3 的 AppManager 狀態介面**

```bash
grep -rn "class AppManager" --include="*.py" .
grep -rn "connected\|reconnecting\|pending-restart" --include="*.py" multi_profile gateway.py | head
```

預期：找到 `AppManager` 及其每 App 狀態存取方法；記錄實際方法名（下文以 `AppManager.app_statuses() -> dict[str, str]` 為假設介面，實作時以計畫 3 為準）。

---

### 任務 1：建立隔離 STS 檢查與 Account ID 遮罩

**文件：**
- 建立：`multi_profile/sts.py`
- 建立：`tests/test_multi_profile_sts.py`
- 修改：`multi_profile/runtime_env.py`
- 修改：`multi_profile/__init__.py`

- [ ] **步驟 1：編寫失敗測試**

建立 `tests/test_multi_profile_sts.py`：

```python
import json
import subprocess

import pytest

from multi_profile.models import ProfileConfig
from multi_profile.sts import (
    StsResult,
    mask_account_id,
    run_sts_check,
)


def make_profile(**changes):
    values = {
        "profile_id": "prod-cn",
        "aws_profile": "production",
        "aws_region": "cn-northwest-1",
        "expected_account_id": "123456789012",
        "working_dir": "/srv/kiro-devops",
    }
    values.update(changes)
    return ProfileConfig(**values)


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["aws"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


def test_mask_account_id_shows_only_last_four_digits():
    assert mask_account_id("123456789012") == "********9012"
    assert len(mask_account_id("123456789012")) == 12


def test_sts_success_returns_account_id():
    seen = {}

    def runner(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs["env"]
        seen["timeout"] = kwargs["timeout"]
        return completed(stdout=json.dumps({"Account": "123456789012"}))

    result = run_sts_check(
        make_profile(),
        base_env={"PATH": "/usr/bin", "AWS_ACCESS_KEY_ID": "AKIA_LEAK"},
        runner=runner,
        timeout_sec=10,
    )

    assert result.ok is True
    assert result.account_id == "123456789012"
    assert result.error_kind is None
    assert seen["argv"] == [
        "aws", "sts", "get-caller-identity",
        "--profile", "production", "--output", "json",
    ]
    assert seen["timeout"] == 10
    # 隔離環境：父程序 credential selectors 被移除，且不修改 base_env
    assert "AWS_ACCESS_KEY_ID" not in seen["env"]
    assert seen["env"]["AWS_PROFILE"] == "production"
    assert seen["env"]["AWS_DEFAULT_PROFILE"] == "production"
    assert seen["env"]["AWS_REGION"] == "cn-northwest-1"


def test_sts_check_does_not_mutate_base_env_or_os_environ():
    base = {"PATH": "/usr/bin", "AWS_PROFILE": "wrong-profile"}

    def runner(argv, **kwargs):
        return completed(stdout=json.dumps({"Account": "123456789012"}))

    run_sts_check(make_profile(), base_env=base, runner=runner, timeout_sec=10)

    assert base == {"PATH": "/usr/bin", "AWS_PROFILE": "wrong-profile"}


def test_sts_timeout_is_classified_transient():
    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd="aws", timeout=10)

    result = run_sts_check(make_profile(), base_env={}, runner=runner, timeout_sec=10)

    assert result.ok is False
    assert result.error_kind == "timeout"
    assert result.account_id is None


def test_missing_aws_cli_profile_is_classified_permanent():
    def runner(argv, **kwargs):
        return completed(returncode=255, stderr="The config profile (ghost) could not be found")

    result = run_sts_check(make_profile(), base_env={}, runner=runner, timeout_sec=10)

    assert result.ok is False
    assert result.error_kind == "profile_not_found"


def test_other_aws_failure_is_classified_transient():
    def runner(argv, **kwargs):
        return completed(returncode=255, stderr="Unable to locate credentials")

    result = run_sts_check(make_profile(), base_env={}, runner=runner, timeout_sec=10)

    assert result.ok is False
    assert result.error_kind == "transient"


def test_unparseable_output_is_transient_failure():
    def runner(argv, **kwargs):
        return completed(stdout="<html>proxy error</html>")

    result = run_sts_check(make_profile(), base_env={}, runner=runner, timeout_sec=10)

    assert result.ok is False
    assert result.error_kind == "transient"


def test_sts_env_comes_from_shared_profile_env_builder():
    """STS 與 Kiro 子程序必須使用同一套環境隔離規則（規格 §9.1）。"""
    from multi_profile.runtime_env import build_profile_env

    env = build_profile_env(
        make_profile(),
        {"PATH": "/usr/bin", "AWS_SESSION_TOKEN": "leak"},
    )

    assert "AWS_SESSION_TOKEN" not in env
    assert env["AWS_PROFILE"] == "production"
    assert env["AWS_SDK_LOAD_CONFIG"] == "1"
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_sts.py
```

預期：FAIL，包含 `ModuleNotFoundError: No module named 'multi_profile.sts'` 與 `ImportError: cannot import name 'build_profile_env'`。

- [ ] **步驟 3：在 runtime_env.py 抽出 build_profile_env**

將 `multi_profile/runtime_env.py` 的環境建立邏輯重構為（`build_child_env` 行為不變，計畫 2 測試必須保持通過）：

```python
def build_profile_env(
    profile: "ProfileConfig",
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    child = dict(os.environ if base_env is None else base_env)
    for name in _AWS_SELECTOR_VARS:
        child.pop(name, None)

    child["AWS_PROFILE"] = profile.aws_profile
    child["AWS_DEFAULT_PROFILE"] = profile.aws_profile
    if profile.aws_region:
        child["AWS_REGION"] = profile.aws_region
        child["AWS_DEFAULT_REGION"] = profile.aws_region
    child["AWS_SDK_LOAD_CONFIG"] = "1"
    child["NO_COLOR"] = "1"
    return child


def build_child_env(
    context: ExecutionContext,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    return build_profile_env(context.profile, base_env)
```

- [ ] **步驟 4：實作 multi_profile/sts.py**

建立 `multi_profile/sts.py`：

```python
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .models import ProfileConfig
from .runtime_env import build_profile_env


@dataclass(frozen=True)
class StsResult:
    ok: bool
    account_id: str | None
    # None | "timeout" | "profile_not_found" | "transient"
    error_kind: str | None
    detail: str


def mask_account_id(account_id: str) -> str:
    """規格 §7.4：固定顯示最後 4 位，例如 ********9012。"""
    return "*" * max(0, len(account_id) - 4) + account_id[-4:]


def _classify_failure(exc: Exception | None, returncode: int, stderr: str) -> str:
    if exc is not None:
        return "timeout"
    lowered = stderr.lower()
    if "could not be found" in lowered and "profile" in lowered:
        return "profile_not_found"
    return "transient"


def run_sts_check(
    profile: ProfileConfig,
    *,
    base_env: Mapping[str, str] | None = None,
    runner: Callable[..., Any] = subprocess.run,
    timeout_sec: int = 10,
) -> StsResult:
    """在隔離子環境執行 STS；永不修改 os.environ 或 base_env。"""
    env = build_profile_env(profile, base_env)
    argv = [
        "aws", "sts", "get-caller-identity",
        "--profile", profile.aws_profile, "--output", "json",
    ]
    try:
        completed = runner(
            argv, capture_output=True, text=True, timeout=timeout_sec, env=env,
        )
    except subprocess.TimeoutExpired:
        return StsResult(False, None, "timeout", f"sts timeout after {timeout_sec}s")
    except OSError as exc:
        return StsResult(False, None, "transient", f"aws cli spawn failed: {exc}")

    if completed.returncode != 0:
        kind = _classify_failure(None, completed.returncode, completed.stderr or "")
        # 只記錄錯誤摘要，不記錄完整 stderr（可能含 endpoint 等雜訊）
        detail = (completed.stderr or "").strip().splitlines()
        return StsResult(False, None, kind, detail[-1][:200] if detail else "sts failed")

    try:
        payload = json.loads(completed.stdout)
        account_id = payload["Account"]
    except (ValueError, KeyError, TypeError):
        return StsResult(False, None, "transient", "sts output is not valid identity json")
    return StsResult(True, str(account_id), None, "ok")
```

在 `multi_profile/__init__.py` 追加：

```python
from .sts import StsResult, mask_account_id, run_sts_check

__all__ += ["StsResult", "mask_account_id", "run_sts_check"]
```

- [ ] **步驟 5：執行 STS 與計畫 2 env 回歸測試**

```bash
pytest -q tests/test_multi_profile_sts.py tests/test_multi_profile_runtime_env.py
```

預期：全部 PASS（計畫 2 的 `build_child_env` 行為不變）。

- [ ] **步驟 6：提交任務 1**

```bash
git add multi_profile/sts.py multi_profile/runtime_env.py multi_profile/__init__.py \
  tests/test_multi_profile_sts.py
git commit -m "feat(多租戶): 加入隔離 STS 檢查與帳號遮罩"
```

---

### 任務 2：解析健康檢查操作預設值

**文件：**
- 建立：`multi_profile/operational_settings.py`
- 建立：`tests/test_multi_profile_operational_settings.py`
- 修改：`multi_profile/__init__.py`
- 修改：`.env.example`

- [ ] **步驟 1：編寫失敗測試**

建立 `tests/test_multi_profile_operational_settings.py`：

```python
import pytest

from multi_profile.operational_settings import (
    OperationalSettings,
    load_operational_settings,
)


def test_defaults_match_spec_section_14_1():
    settings = load_operational_settings({})

    assert settings.sts_timeout_sec == 10
    assert settings.health_check_interval_sec == 600
    assert settings.health_grace_sec == 1800
    assert settings.health_jitter_max_sec == 60
    assert settings.revision_keep == 20


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AWS_STS_TIMEOUT_SEC", "2"),      # 允許 3–60
        ("AWS_STS_TIMEOUT_SEC", "61"),
        ("PROFILE_HEALTH_CHECK_INTERVAL_SEC", "59"),   # 允許 60–3600
        ("PROFILE_HEALTH_CHECK_INTERVAL_SEC", "3601"),
        ("PROFILE_HEALTH_GRACE_SEC", "-1"),            # 允許 0–86400
        ("PROFILE_HEALTH_GRACE_SEC", "86401"),
        ("AWS_STS_TIMEOUT_SEC", "not-a-number"),
    ],
)
def test_out_of_range_or_invalid_values_are_rejected(key, value):
    with pytest.raises(ValueError, match=key):
        load_operational_settings({key: value})


def test_boundary_values_are_accepted():
    settings = load_operational_settings({
        "AWS_STS_TIMEOUT_SEC": "3",
        "PROFILE_HEALTH_CHECK_INTERVAL_SEC": "3600",
        "PROFILE_HEALTH_GRACE_SEC": "0",
    })

    assert settings.sts_timeout_sec == 3
    assert settings.health_check_interval_sec == 3600
    assert settings.health_grace_sec == 0
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_operational_settings.py
```

預期：FAIL，包含 `ModuleNotFoundError`。

- [ ] **步驟 3：實作 operational_settings.py**

建立 `multi_profile/operational_settings.py`：

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class OperationalSettings:
    sts_timeout_sec: int = 10
    health_check_interval_sec: int = 600
    health_grace_sec: int = 1800
    health_jitter_max_sec: int = 60
    revision_keep: int = 20


_BOUNDS = {
    "AWS_STS_TIMEOUT_SEC": (3, 60, "sts_timeout_sec"),
    "PROFILE_HEALTH_CHECK_INTERVAL_SEC": (60, 3600, "health_check_interval_sec"),
    "PROFILE_HEALTH_GRACE_SEC": (0, 86400, "health_grace_sec"),
}


def _bounded(values: Mapping[str, str], key: str) -> int | None:
    raw = values.get(key, "").strip()
    if not raw:
        return None
    low, high, _ = _BOUNDS[key]
    try:
        number = int(raw)
    except ValueError:
        raise ValueError(f"{key} must be an integer between {low} and {high}") from None
    if not low <= number <= high:
        raise ValueError(f"{key} must be between {low} and {high}, got {number}")
    return number


def load_operational_settings(
    environ: Mapping[str, str] | None = None,
) -> OperationalSettings:
    values = environ if environ is not None else os.environ
    overrides = {}
    for key, (_, _, field) in _BOUNDS.items():
        number = _bounded(values, key)
        if number is not None:
            overrides[field] = number
    return OperationalSettings(**overrides)
```

在 `multi_profile/__init__.py` 追加：

```python
from .operational_settings import OperationalSettings, load_operational_settings

__all__ += ["OperationalSettings", "load_operational_settings"]
```

- [ ] **步驟 4：在 `.env.example` 加入健康檢查設定**

在計畫 1 加入的多 profile 區塊尾端追加：

```dotenv
# Profile 健康檢查（規格 §14.1，皆有界限；Dashboard 會顯示實際值）
# AWS_STS_TIMEOUT_SEC=10                # 允許 3–60 秒
# PROFILE_HEALTH_CHECK_INTERVAL_SEC=600 # 允許 60–3600 秒；每輪加 0–60 秒 jitter
# PROFILE_HEALTH_GRACE_SEC=1800         # 允許 0–86400 秒；只適用暫時性 STS 錯誤
# Revision 目錄（預設 <project>/runtime/config-revisions/multi-profile/）
# MULTI_PROFILE_REVISION_DIR=/home/ubuntu/kiro-devops/runtime/config-revisions/multi-profile
```

- [ ] **步驟 5：執行測試並提交**

```bash
pytest -q tests/test_multi_profile_operational_settings.py
```

預期：4 passed。

```bash
git add multi_profile/operational_settings.py multi_profile/__init__.py \
  tests/test_multi_profile_operational_settings.py .env.example
git commit -m "feat(多租戶): 解析健康檢查操作預設值"
```

---

### 任務 3：建立 ProfileHealthMonitor 狀態機

**文件：**
- 建立：`multi_profile/health.py`
- 建立：`tests/test_multi_profile_health.py`
- 修改：`multi_profile/__init__.py`

狀態語意（規格 §14）：

- `active`：最近 STS 成功且 Account ID 相符。
- `degraded`：暫時性 STS 失敗（timeout／transient），仍在 grace period 內。
- `blocked`：Account ID 不符、AWS CLI profile 不存在、或暫時性失敗持續超過 grace period；**Account ID 不符立即 blocked，不適用 grace**。
- `disabled`：設定中 `enabled: false`，Monitor 不對其執行 STS。

- [ ] **步驟 1：編寫失敗的狀態機測試**

建立 `tests/test_multi_profile_health.py`：

```python
import pytest

from multi_profile.health import (
    ProfileHealthMonitor,
    ProfileUnavailable,
)
from multi_profile.models import ProfileConfig, create_snapshot, AppConfig
from multi_profile.sts import StsResult


def make_snapshot(*profiles, generation=1):
    apps = {
        "ops-bot": AppConfig(
            app_key="ops-bot",
            app_id_env="FEISHU_OPS_APP_ID",
            app_secret_env="FEISHU_OPS_APP_SECRET",
            default_profile=profiles[0].profile_id,
        )
    }
    return create_snapshot(generation, apps, {p.profile_id: p for p in profiles}, ())


def make_profile(profile_id="prod-cn", **changes):
    values = {
        "profile_id": profile_id,
        "aws_profile": "production",
        "expected_account_id": "123456789012",
        "working_dir": "/srv/kiro-devops",
    }
    values.update(changes)
    return ProfileConfig(**values)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_monitor(snapshot, clock, sts_results, settings_changes=None):
    from multi_profile.operational_settings import OperationalSettings

    settings = OperationalSettings(**(settings_changes or {}))

    def sts_runner(profile, **kwargs):
        return sts_results[profile.profile_id].pop(0)

    return ProfileHealthMonitor(
        lambda: snapshot,
        settings=settings,
        clock=clock,
        sts_runner=sts_runner,
    )


def ok(account="123456789012"):
    return StsResult(True, account, None, "ok")


def timeout():
    return StsResult(False, None, "timeout", "sts timeout after 10s")


def test_successful_sts_marks_profile_active_with_masked_account():
    clock = FakeClock()
    monitor = make_monitor(
        make_snapshot(make_profile()), clock, {"prod-cn": [ok()]},
    )

    monitor.check_all_now()
    health = monitor.health("prod-cn")

    assert health.state == "active"
    assert health.account_id_masked == "********9012"
    assert health.last_sts_at == 1000.0
    monitor.ensure_usable("prod-cn")  # 不拋出


def test_account_id_mismatch_blocks_immediately_without_grace():
    clock = FakeClock()
    monitor = make_monitor(
        make_snapshot(make_profile()), clock, {"prod-cn": [ok(account="999999999999")]},
    )

    monitor.check_all_now()
    health = monitor.health("prod-cn")

    assert health.state == "blocked"
    assert health.last_error == "account_mismatch"
    with pytest.raises(ProfileUnavailable, match="blocked"):
        monitor.ensure_usable("prod-cn")


def test_missing_aws_profile_blocks_immediately():
    clock = FakeClock()
    monitor = make_monitor(
        make_snapshot(make_profile()), clock,
        {"prod-cn": [StsResult(False, None, "profile_not_found", "could not be found")]},
    )

    monitor.check_all_now()

    assert monitor.health("prod-cn").state == "blocked"
    assert monitor.health("prod-cn").last_error == "profile_not_found"


def test_transient_failure_within_grace_is_degraded_then_recovers():
    clock = FakeClock()
    monitor = make_monitor(
        make_snapshot(make_profile()), clock, {"prod-cn": [ok(), timeout(), ok()]},
    )
    monitor.check_all_now()  # active

    clock.advance(60)
    monitor.check_all_now()
    health = monitor.health("prod-cn")
    assert health.state == "degraded"
    assert health.consecutive_failures == 1
    monitor.ensure_usable("prod-cn")  # degraded 仍允許新任務

    clock.advance(60)
    monitor.check_all_now()
    assert monitor.health("prod-cn").state == "active"
    assert monitor.health("prod-cn").consecutive_failures == 0


def test_transient_failure_beyond_grace_becomes_blocked():
    clock = FakeClock()
    monitor = make_monitor(
        make_snapshot(make_profile()),
        clock,
        {"prod-cn": [ok(), timeout(), timeout()]},
        settings_changes={"health_grace_sec": 100},
    )
    monitor.check_all_now()

    clock.advance(50)
    monitor.check_all_now()
    assert monitor.health("prod-cn").state == "degraded"

    clock.advance(60)  # 首次失敗至今 110s > grace 100s
    monitor.check_all_now()
    assert monitor.health("prod-cn").state == "blocked"


def test_disabled_profile_is_disabled_and_never_checked():
    clock = FakeClock()
    profile = make_profile(enabled=False)
    monitor = make_monitor(
        make_snapshot(profile), clock, {"prod-cn": []},  # 不應被呼叫
    )

    monitor.check_all_now()

    assert monitor.health("prod-cn").state == "disabled"


def test_unknown_profile_is_unavailable():
    clock = FakeClock()
    monitor = make_monitor(make_snapshot(make_profile()), clock, {"prod-cn": [ok()]})
    monitor.check_all_now()

    with pytest.raises(ProfileUnavailable, match="unknown profile"):
        monitor.ensure_usable("ghost")


def test_monitor_never_switches_profiles():
    """規格 §14：Health Monitor 不得自動改用其他 profile；只能回報狀態。"""
    clock = FakeClock()
    snapshot = make_snapshot(make_profile(), make_profile("backup"))
    monitor = make_monitor(
        snapshot, clock,
        {"prod-cn": [ok(account="999999999999")], "backup": [ok()]},
    )

    monitor.check_all_now()

    assert monitor.health("prod-cn").state == "blocked"
    assert monitor.health("backup").state == "active"
    # 對 blocked profile 的拒絕不含任何替代建議
    with pytest.raises(ProfileUnavailable) as exc_info:
        monitor.ensure_usable("prod-cn")
    assert "backup" not in str(exc_info.value)


def test_config_reload_reconciles_profile_set():
    clock = FakeClock()
    first = make_snapshot(make_profile(), generation=1)
    monitor = make_monitor(first, clock, {"prod-cn": [ok()]})
    monitor.check_all_now()
    assert monitor.health("prod-cn").state == "active"

    # 熱載入：prod-cn 被移除，新增 eu
    second = make_snapshot(make_profile("eu"), generation=2)
    monitor.on_config_reload(second)

    statuses = monitor.statuses()
    assert "prod-cn" not in statuses
    assert statuses["eu"].state == "active"  # 新 profile 在首次檢查前樂觀視為 active
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_health.py
```

預期：FAIL，包含 `ModuleNotFoundError: No module named 'multi_profile.health'`。

- [ ] **步驟 3：實作 health.py**

建立 `multi_profile/health.py`：

```python
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .models import ConfigSnapshot, ProfileConfig
from .operational_settings import OperationalSettings
from .sts import StsResult, mask_account_id, run_sts_check


STATE_ACTIVE = "active"
STATE_DEGRADED = "degraded"
STATE_BLOCKED = "blocked"
STATE_DISABLED = "disabled"


class ProfileUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ProfileHealth:
    profile_id: str
    state: str
    account_id_masked: str | None
    last_sts_at: float | None
    last_error: str | None
    consecutive_failures: int


@dataclass
class _HealthState:
    state: str = STATE_ACTIVE
    account_id_masked: str | None = None
    last_sts_at: float | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    first_failure_at: float | None = None


class ProfileHealthMonitor:
    """週期性 STS 健康檢查。只回報狀態，永不自動切換 profile（規格 §14）。"""

    def __init__(
        self,
        snapshot_getter: Callable[[], ConfigSnapshot],
        *,
        settings: OperationalSettings | None = None,
        clock: Callable[[], float] = time.time,
        sts_runner: Callable[..., StsResult] = run_sts_check,
        jitter: Callable[[float], float] = random.uniform,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ):
        self._snapshot_getter = snapshot_getter
        self._settings = settings or OperationalSettings()
        self._clock = clock
        self._sts_runner = sts_runner
        self._jitter = jitter
        self._thread_factory = thread_factory
        self._states: dict[str, _HealthState] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- 檢查邏輯 -------------------------------------------------

    def check_all_now(self) -> None:
        snapshot = self._snapshot_getter()
        for profile in snapshot.profiles.values():
            self._check_one(profile)

    def _check_one(self, profile: ProfileConfig) -> None:
        if not profile.enabled:
            with self._lock:
                self._states[profile.profile_id] = _HealthState(state=STATE_DISABLED)
            return

        result = self._sts_runner(
            profile, timeout_sec=self._settings.sts_timeout_sec,
        )
        now = self._clock()
        with self._lock:
            state = self._states.setdefault(profile.profile_id, _HealthState())
            if result.ok and result.account_id == profile.expected_account_id:
                state.state = STATE_ACTIVE
                state.account_id_masked = mask_account_id(result.account_id)
                state.last_sts_at = now
                state.last_error = None
                state.consecutive_failures = 0
                state.first_failure_at = None
                return

            state.last_sts_at = now
            if result.ok:
                # Account ID 不符：立即 blocked，不適用 grace（規格 §14）
                state.state = STATE_BLOCKED
                state.last_error = "account_mismatch"
                state.account_id_masked = (
                    mask_account_id(result.account_id) if result.account_id else None
                )
            elif result.error_kind == "profile_not_found":
                state.state = STATE_BLOCKED
                state.last_error = "profile_not_found"
            else:
                # 暫時性失敗：grace 內 degraded，超過 grace blocked
                state.consecutive_failures += 1
                if state.first_failure_at is None:
                    state.first_failure_at = now
                grace = self._settings.health_grace_sec
                if now - state.first_failure_at > grace:
                    state.state = STATE_BLOCKED
                else:
                    state.state = STATE_DEGRADED
                state.last_error = result.error_kind

    # ---- 查詢與閘門 -----------------------------------------------

    def health(self, profile_id: str) -> ProfileHealth:
        with self._lock:
            state = self._states.get(profile_id)
            if state is None:
                # 尚未檢查過：樂觀視為 active，由下一輪檢查修正
                return ProfileHealth(profile_id, STATE_ACTIVE, None, None, None, 0)
            return ProfileHealth(
                profile_id=profile_id,
                state=state.state,
                account_id_masked=state.account_id_masked,
                last_sts_at=state.last_sts_at,
                last_error=state.last_error,
                consecutive_failures=state.consecutive_failures,
            )

    def statuses(self) -> dict[str, ProfileHealth]:
        snapshot = self._snapshot_getter()
        return {pid: self.health(pid) for pid in snapshot.profiles}

    def ensure_usable(self, profile_id: str) -> None:
        """新任務閘門：blocked/disabled/未知 profile 一律拒絕，不提供替代。"""
        snapshot = self._snapshot_getter()
        profile = snapshot.profiles.get(profile_id)
        if profile is None:
            raise ProfileUnavailable(f"unknown profile: {profile_id}")
        health = self.health(profile_id)
        if health.state == STATE_DISABLED:
            raise ProfileUnavailable(f"profile is disabled: {profile_id}")
        if health.state == STATE_BLOCKED:
            raise ProfileUnavailable(
                f"profile is blocked: {profile_id} ({health.last_error})"
            )

    def on_config_reload(self, snapshot: ConfigSnapshot) -> None:
        """熱載入後對齊 profile 集合：移除已刪除者，新增者下輪檢查。"""
        with self._lock:
            for pid in list(self._states):
                if pid not in snapshot.profiles:
                    del self._states[pid]

    # ---- 背景排程 -------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = self._thread_factory(
            target=self._run_loop,
            name="profile-health-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.check_all_now()
            except Exception:
                # 單輪失敗不得終止監控執行緒；狀態保留至下輪
                pass
            delay = (
                self._settings.health_check_interval_sec
                + self._jitter(self._settings.health_jitter_max_sec)
            )
            self._stop_event.wait(delay)
```

在 `multi_profile/__init__.py` 追加：

```python
from .health import ProfileHealth, ProfileHealthMonitor, ProfileUnavailable

__all__ += ["ProfileHealth", "ProfileHealthMonitor", "ProfileUnavailable"]
```

- [ ] **步驟 4：執行測試**

```bash
pytest -q tests/test_multi_profile_health.py tests/test_multi_profile_sts.py
```

預期：全部 PASS。

- [ ] **步驟 5：提交任務 3**

```bash
git add multi_profile/health.py multi_profile/__init__.py tests/test_multi_profile_health.py
git commit -m "feat(多租戶): 加入 Profile 健康狀態機"
```

---

### 任務 4：建立 RevisionStore 與原子檔案寫入

**文件：**
- 建立：`multi_profile/revisions.py`
- 建立：`tests/test_multi_profile_revisions.py`
- 修改：`multi_profile/__init__.py`

- [ ] **步驟 1：編寫失敗測試**

建立 `tests/test_multi_profile_revisions.py`：

```python
import json
import os

import pytest

from multi_profile.revisions import (
    RevisionStore,
    atomic_write,
    config_checksum,
    revision_dir_from_env,
)


CURRENT = "version: 1\napps: {}\nprofiles: {}\nroutes: []\n"


def test_atomic_write_replaces_content(tmp_path):
    path = tmp_path / "config.yaml"
    atomic_write(path, "first")
    atomic_write(path, "second")

    assert path.read_text(encoding="utf-8") == "second"
    # 不殘留暫存檔
    assert [p.name for p in tmp_path.iterdir()] == ["config.yaml"]


def test_atomic_write_uses_same_directory_temp_and_replace(tmp_path):
    """fsync + os.replace 必須發生，且暫存檔與目標同目錄（跨檔案系統 replace 會失敗）。"""
    path = tmp_path / "config.yaml"
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((src, dst))
        return real_replace(src, dst)

    import multi_profile.revisions as revisions

    original = revisions.os.replace
    revisions.os.replace = spy_replace
    try:
        atomic_write(path, "data")
    finally:
        revisions.os.replace = original

    assert len(calls) == 1
    src, dst = calls[0]
    assert os.path.dirname(src) == str(tmp_path)
    assert dst == str(path)


def test_checksum_is_stable_sha256(tmp_path):
    assert config_checksum("abc") == config_checksum("abc")
    assert config_checksum("abc") != config_checksum("abd")
    assert len(config_checksum("abc")) == 64


def test_revision_dir_defaults_under_runtime(tmp_path):
    assert revision_dir_from_env({}, project_dir=tmp_path) == (
        tmp_path / "runtime" / "config-revisions" / "multi-profile"
    )
    custom = tmp_path / "custom"
    assert revision_dir_from_env(
        {"MULTI_PROFILE_REVISION_DIR": str(custom)}, project_dir=tmp_path,
    ) == custom


def test_save_list_read_roundtrip(tmp_path):
    store = RevisionStore(tmp_path / "revs")
    info = store.save(
        CURRENT, generation=3, source="publish", validation_summary="8/8 stages ok",
    )

    listed = store.list()
    assert [r.revision_id for r in listed] == [info.revision_id]
    assert listed[0].generation == 3
    assert listed[0].source == "publish"
    assert listed[0].checksum == config_checksum(CURRENT)
    assert store.read(info.revision_id) == CURRENT
    # revision id 嵌入 generation 與 checksum 前綴，方便人工辨識
    assert "gen3" in info.revision_id
    assert info.revision_id.endswith(config_checksum(CURRENT)[:8])


def test_prune_keeps_only_newest_20_revisions(tmp_path):
    store = RevisionStore(tmp_path / "revs")
    for generation in range(1, 26):
        store.save(
            f"# gen {generation}\n{CURRENT}", generation=generation,
            source="publish", validation_summary="ok",
        )
        store.prune(keep=20)

    listed = store.list()
    assert len(listed) == 20
    generations = {r.generation for r in listed}
    assert generations == set(range(6, 26))


def test_diff_against_current_and_against_other_revision(tmp_path):
    store = RevisionStore(tmp_path / "revs")
    old = store.save("# old\n" + CURRENT, generation=1, source="publish", validation_summary="ok")
    new = store.save("# new\n" + CURRENT, generation=2, source="publish", validation_summary="ok")

    diff = store.diff(old.revision_id, against_text="# new\n" + CURRENT)
    assert "-# old" in diff
    assert "+# new" in diff

    diff_two = store.diff(old.revision_id, against_revision=new.revision_id)
    assert diff_two == diff


def test_last_known_good_updated_atomically(tmp_path):
    store = RevisionStore(tmp_path / "revs")
    store.update_last_known_good(CURRENT)

    lkg = tmp_path / "revs" / "last-known-good.yaml"
    assert lkg.read_text(encoding="utf-8") == CURRENT


def test_unknown_revision_raises(tmp_path):
    store = RevisionStore(tmp_path / "revs")
    with pytest.raises(KeyError):
        store.read("no-such-revision")


def test_revision_metadata_contains_no_secret_values(tmp_path):
    store = RevisionStore(tmp_path / "revs")
    info = store.save(
        CURRENT, generation=1, source="publish", validation_summary="ok",
    )
    meta = json.loads((tmp_path / "revs" / f"{info.revision_id}.json").read_text())
    assert set(meta) == {
        "revision_id", "created_at", "generation", "checksum", "source",
        "validation_summary",
    }
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_revisions.py
```

預期：FAIL，包含 `ModuleNotFoundError`。

- [ ] **步驟 3：實作 revisions.py**

建立 `multi_profile/revisions.py`：

```python
from __future__ import annotations

import difflib
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


LAST_KNOWN_GOOD = "last-known-good.yaml"


@dataclass(frozen=True)
class RevisionInfo:
    revision_id: str
    created_at: str
    generation: int
    checksum: str
    source: str  # "publish" | "rollback" | "bootstrap"
    validation_summary: str


def config_checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def revision_dir_from_env(
    environ: Mapping[str, str], *, project_dir: str | Path,
) -> Path:
    configured = environ.get("MULTI_PROFILE_REVISION_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(project_dir) / "runtime" / "config-revisions" / "multi-profile"


def atomic_write(path: str | Path, text: str) -> None:
    """同目錄暫存檔 → flush + fsync → os.replace → fsync 目錄（規格 §13.4）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    dir_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


class RevisionStore:
    """保存非敏感設定 revision：YAML 本文 + JSON metadata（規格 §16、§20.1）。"""

    def __init__(self, revision_dir: str | Path):
        self._dir = Path(revision_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        return self._dir

    def save(
        self,
        yaml_text: str,
        *,
        generation: int,
        source: str,
        validation_summary: str,
    ) -> RevisionInfo:
        checksum = config_checksum(yaml_text)
        created = datetime.now(timezone.utc)
        revision_id = (
            f"{created.strftime('%Y%m%dT%H%M%SZ')}-gen{generation}-{checksum[:8]}"
        )
        info = RevisionInfo(
            revision_id=revision_id,
            created_at=created.isoformat(),
            generation=generation,
            checksum=checksum,
            source=source,
            validation_summary=validation_summary,
        )
        atomic_write(self._dir / f"{revision_id}.yaml", yaml_text)
        atomic_write(
            self._dir / f"{revision_id}.json",
            json.dumps(info.__dict__, ensure_ascii=False, indent=2),
        )
        return info

    def list(self) -> list[RevisionInfo]:
        infos = []
        for meta_path in sorted(self._dir.glob("*.json")):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                infos.append(RevisionInfo(**data))
            except (ValueError, TypeError, KeyError):
                continue
        return infos

    def read(self, revision_id: str) -> str:
        path = self._dir / f"{self._guard(revision_id)}.yaml"
        if not path.is_file():
            raise KeyError(f"unknown revision: {revision_id}")
        return path.read_text(encoding="utf-8")

    def diff(
        self,
        revision_id: str,
        *,
        against_text: str | None = None,
        against_revision: str | None = None,
    ) -> str:
        old = self.read(revision_id)
        if against_revision is not None:
            new = self.read(against_revision)
        elif against_text is not None:
            new = against_text
        else:
            raise ValueError("diff requires against_text or against_revision")
        return "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f"revision:{revision_id}",
                tofile=against_revision or "current",
            )
        )

    def update_last_known_good(self, yaml_text: str) -> None:
        atomic_write(self._dir / LAST_KNOWN_GOOD, yaml_text)

    def prune(self, keep: int = 20) -> None:
        infos = self.list()
        for stale in infos[:-keep] if len(infos) > keep else []:
            for suffix in (".yaml", ".json"):
                try:
                    (self._dir / f"{stale.revision_id}{suffix}").unlink()
                except OSError:
                    pass

    @staticmethod
    def _guard(revision_id: str) -> str:
        # 防止路徑穿越；revision id 只允許安全字元
        if not revision_id or any(c in revision_id for c in "/\\.. "):
            raise KeyError(f"invalid revision id: {revision_id!r}")
        return revision_id
```

在 `multi_profile/__init__.py` 追加：

```python
from .revisions import RevisionInfo, RevisionStore, atomic_write, config_checksum, revision_dir_from_env

__all__ += [
    "RevisionInfo",
    "RevisionStore",
    "atomic_write",
    "config_checksum",
    "revision_dir_from_env",
]
```

- [ ] **步驟 4：驗證 runtime/ 已被 gitignore**

```bash
grep -n "^runtime/$" .gitignore
git check-ignore -v runtime/config-revisions/multi-profile/last-known-good.yaml || true
```

預期：`.gitignore` 第 1 段有 `runtime/`（計畫 1 已加入）；第 2 段顯示命中規則。若未命中，停止並回到計畫 1 補上，不得在本計畫重複新增。

- [ ] **步驟 5：執行測試並提交**

```bash
pytest -q tests/test_multi_profile_revisions.py
```

預期：全部 PASS。

```bash
git add multi_profile/revisions.py multi_profile/__init__.py tests/test_multi_profile_revisions.py
git commit -m "feat(多租戶): 加入設定 revision 儲存與原子寫入"
```

---

### 任務 5：建立外部驗證管線（規格 §13.3）

**文件：**
- 建立：`multi_profile/external_validation.py`
- 建立：`tests/test_multi_profile_external_validation.py`
- 修改：`multi_profile/__init__.py`

管線順序固定（任何一步失敗即整體失敗，規格 §13.3）：

1. `yaml_schema`、`env_refs`、`referential_integrity`、`paths_timeouts`：由計畫 1 的 `load_config` 一次涵蓋（步驟 1–4）。
2. `kiro_agent_model`：Agent 檔存在於 `~/.kiro/agents/<name>.json`（比照 `dashboard/kiro_scanner.py` 的 `AGENTS_DIR`）；有指定 `model`／`alert_model` 時向 `kiro-cli chat --list-models --format json` 核對。
3. `aws_cli_profile`：解析 `~/.aws/config` 與 `~/.aws/credentials`，確認 profile 存在。
4. `sts_identity`：以任務 1 的 `run_sts_check` 隔離執行。
5. `expected_account`：核對 STS Account 與 `expected_account_id`。

- [ ] **步驟 1：編寫失敗測試**

建立 `tests/test_multi_profile_external_validation.py`：

```python
import json

from multi_profile.external_validation import run_validation_pipeline
from multi_profile.sts import StsResult


VALID_YAML = """
version: 1
apps:
  ops-bot:
    app_id_env: FEISHU_OPS_APP_ID
    app_secret_env: FEISHU_OPS_APP_SECRET
    default_profile: prod-cn
profiles:
  prod-cn:
    aws_profile: production
    aws_region: cn-northwest-1
    expected_account_id: "123456789012"
    kiro_agent: my-dev-bot
    model: claude-sonnet
    working_dir: {working_dir}
routes:
  - app: ops-bot
    chat_id: oc_prod
    profile: prod-cn
"""


def make_env(tmp_path):
    (tmp_path / "kiro" / "agents").mkdir(parents=True)
    (tmp_path / "kiro" / "agents" / "my-dev-bot.json").write_text(
        json.dumps({"name": "my-dev-bot"})
    )
    (tmp_path / "aws").mkdir()
    (tmp_path / "aws" / "config").write_text(
        "[profile production]\nregion = cn-northwest-1\n"
    )
    return {
        "environ": {
            "FEISHU_OPS_APP_ID": "cli_test",
            "FEISHU_OPS_APP_SECRET": "secret_test",
        },
        "kiro_agents_dir": tmp_path / "kiro" / "agents",
        "aws_config_dir": tmp_path / "aws",
        "model_lister": lambda: ["claude-sonnet", "claude-opus"],
        "sts_runner": lambda profile, **kw: StsResult(True, "123456789012", None, "ok"),
    }


def stage_names(report):
    return [s.stage for s in report.stages]


def test_full_pipeline_passes_in_spec_order(tmp_path):
    report = run_validation_pipeline(
        VALID_YAML.format(working_dir=tmp_path), **make_env(tmp_path),
    )

    assert report.ok is True
    assert stage_names(report) == [
        "yaml_schema",
        "env_refs",
        "referential_integrity",
        "paths_timeouts",
        "kiro_agent_model",
        "aws_cli_profile",
        "sts_identity",
        "expected_account",
    ]
    assert all(s.ok for s in report.stages)


def test_schema_failure_short_circuits_external_stages(tmp_path):
    report = run_validation_pipeline("version: [", **make_env(tmp_path))

    assert report.ok is False
    assert report.stages[0].stage == "yaml_schema"
    assert report.stages[0].ok is False
    # 外部階段不執行（不浪費 STS 呼叫）
    assert "sts_identity" not in stage_names(report)


def test_missing_kiro_agent_fails_before_aws_stages(tmp_path):
    env = make_env(tmp_path)
    (tmp_path / "kiro" / "agents" / "my-dev-bot.json").unlink()

    report = run_validation_pipeline(
        VALID_YAML.format(working_dir=tmp_path), **env,
    )

    assert report.ok is False
    kiro_stage = next(s for s in report.stages if s.stage == "kiro_agent_model")
    assert kiro_stage.ok is False
    assert "my-dev-bot" in kiro_stage.detail
    assert "aws_cli_profile" not in stage_names(report)


def test_unavailable_model_fails_validation(tmp_path):
    env = make_env(tmp_path)
    env["model_lister"] = lambda: ["claude-opus"]

    report = run_validation_pipeline(
        VALID_YAML.format(working_dir=tmp_path), **env,
    )

    stage = next(s for s in report.stages if s.stage == "kiro_agent_model")
    assert stage.ok is False
    assert "claude-sonnet" in stage.detail


def test_missing_aws_cli_profile_blocks_before_sts(tmp_path):
    env = make_env(tmp_path)
    (tmp_path / "aws" / "config").write_text("[profile other]\n")
    called = []
    env["sts_runner"] = lambda *a, **kw: called.append(1)

    report = run_validation_pipeline(
        VALID_YAML.format(working_dir=tmp_path), **env,
    )

    assert report.ok is False
    stage = next(s for s in report.stages if s.stage == "aws_cli_profile")
    assert stage.ok is False
    assert called == []  # profile 不存在就不呼叫 STS


def test_sts_timeout_fails_sts_stage(tmp_path):
    env = make_env(tmp_path)
    env["sts_runner"] = lambda profile, **kw: StsResult(False, None, "timeout", "t/o")

    report = run_validation_pipeline(
        VALID_YAML.format(working_dir=tmp_path), **env,
    )

    stage = next(s for s in report.stages if s.stage == "sts_identity")
    assert stage.ok is False
    assert "expected_account" not in stage_names(report)


def test_account_mismatch_fails_final_stage(tmp_path):
    env = make_env(tmp_path)
    env["sts_runner"] = lambda profile, **kw: StsResult(True, "999999999999", None, "ok")

    report = run_validation_pipeline(
        VALID_YAML.format(working_dir=tmp_path), **env,
    )

    stage = next(s for s in report.stages if s.stage == "expected_account")
    assert stage.ok is False
    # detail 只含遮罩帳號，絕不含 Secret
    assert "********9999" in stage.detail
    assert "999999999999" not in stage.detail
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_external_validation.py
```

預期：FAIL，包含 `ModuleNotFoundError`。

- [ ] **步驟 3：實作 external_validation.py**

建立 `multi_profile/external_validation.py`：

```python
from __future__ import annotations

import configparser
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .config_loader import ConfigError, load_config
from .models import ConfigSnapshot
from .sts import StsResult, mask_account_id, run_sts_check


@dataclass(frozen=True)
class StageResult:
    stage: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    stages: tuple[StageResult, ...]
    snapshot: ConfigSnapshot | None  # 僅成功時提供，供 publisher 重用


def _default_model_lister() -> list[str]:
    kiro_bin = shutil.which("kiro-cli") or "kiro-cli"
    try:
        result = subprocess.run(
            [kiro_bin, "chat", "--list-models", "--format", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        models = data.get("models", data if isinstance(data, list) else [])
        return [m.get("id") or m.get("name") for m in models if isinstance(m, dict)]
    except Exception:
        return []


def _load_snapshot_from_text(
    yaml_text: str, environ: Mapping[str, str],
) -> tuple[ConfigSnapshot | None, str | None, str | None]:
    """把 Draft 寫入暫存檔並重用計畫 1 的 load_config（步驟 1–4）。

    回傳 (snapshot, failed_stage, detail)；成功時後兩者為 None。
    """
    fd, tmp_name = tempfile.mkstemp(prefix="draft-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(yaml_text)
        try:
            return load_config(tmp_name, environ=environ, generation=0), None, None
        except ConfigError as exc:
            message = str(exc)
            if message.startswith("invalid YAML") or "unknown field" in message \
                    or "must be" in message and "env" not in message:
                # YAML 語法與 schema 類錯誤
                stage = "yaml_schema"
            elif "env" in message:
                stage = "env_refs"
            elif "references" in message or "duplicate route" in message:
                stage = "referential_integrity"
            else:
                stage = "paths_timeouts"
            return None, stage, message
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def _check_kiro_agent_model(
    snapshot: ConfigSnapshot,
    kiro_agents_dir: Path,
    model_lister: Callable[[], list[str]],
) -> StageResult:
    agents = {
        path.stem for path in Path(kiro_agents_dir).glob("*.json")
    } if Path(kiro_agents_dir).is_dir() else set()
    needed = set()
    for profile in snapshot.profiles.values():
        if profile.enabled:
            if profile.kiro_agent:
                needed.add(profile.kiro_agent)
            if profile.alert_agent:
                needed.add(profile.alert_agent)
    missing = sorted(needed - agents)
    if missing:
        return StageResult(
            "kiro_agent_model", False, f"missing kiro agent(s): {', '.join(missing)}",
        )

    wanted_models = set()
    for profile in snapshot.profiles.values():
        if profile.enabled:
            for model in (profile.model, profile.alert_model):
                if model:
                    wanted_models.add(model)
    if wanted_models:
        available = set(model_lister())
        unavailable = sorted(wanted_models - available)
        if unavailable:
            return StageResult(
                "kiro_agent_model", False,
                f"unavailable model(s): {', '.join(unavailable)}",
            )
    return StageResult("kiro_agent_model", True, "agents and models available")


def _check_aws_cli_profiles(
    snapshot: ConfigSnapshot, aws_config_dir: Path,
) -> StageResult:
    parser = configparser.RawConfigParser()
    parser.read([
        Path(aws_config_dir) / "credentials",
        Path(aws_config_dir) / "config",
    ])
    known = set()
    for section in parser.sections():
        known.add(section)
        if section.startswith("profile "):
            known.add(section[len("profile "):])
    needed = {
        p.aws_profile for p in snapshot.profiles.values() if p.enabled
    }
    missing = sorted(needed - known)
    if missing:
        return StageResult(
            "aws_cli_profile", False,
            f"missing AWS CLI profile(s): {', '.join(missing)}",
        )
    return StageResult("aws_cli_profile", True, "all AWS CLI profiles exist")


def run_validation_pipeline(
    yaml_text: str,
    *,
    environ: Mapping[str, str] | None = None,
    kiro_agents_dir: str | Path | None = None,
    aws_config_dir: str | Path | None = None,
    model_lister: Callable[[], list[str]] | None = None,
    sts_runner: Callable[..., StsResult] = run_sts_check,
    sts_timeout_sec: int = 10,
) -> ValidationReport:
    """規格 §13.3 完整驗證；任何一步失敗即停止，外部階段不重試。"""
    environ = environ if environ is not None else os.environ
    kiro_agents_dir = Path(kiro_agents_dir or Path.home() / ".kiro" / "agents")
    aws_config_dir = Path(aws_config_dir or Path.home() / ".aws")
    model_lister = model_lister or _default_model_lister

    snapshot, failed_stage, detail = _load_snapshot_from_text(yaml_text, environ)
    if snapshot is None:
        # 步驟 1–4 由 loader 一次完成；回報失敗的那一階段
        prefix_stages = ["yaml_schema", "env_refs", "referential_integrity", "paths_timeouts"]
        stages = tuple(
            StageResult(name, False, detail if name == failed_stage else "skipped")
            if name == failed_stage else StageResult(name, True, "ok")
            for name in prefix_stages[: prefix_stages.index(failed_stage) + 1]
        )
        return ValidationReport(False, stages, None)

    stages = [
        StageResult("yaml_schema", True, "ok"),
        StageResult("env_refs", True, "ok"),
        StageResult("referential_integrity", True, "ok"),
        StageResult("paths_timeouts", True, "ok"),
    ]

    kiro_stage = _check_kiro_agent_model(snapshot, kiro_agents_dir, model_lister)
    stages.append(kiro_stage)
    if not kiro_stage.ok:
        return ValidationReport(False, tuple(stages), None)

    aws_stage = _check_aws_cli_profiles(snapshot, aws_config_dir)
    stages.append(aws_stage)
    if not aws_stage.ok:
        return ValidationReport(False, tuple(stages), None)

    for profile in snapshot.profiles.values():
        if not profile.enabled:
            continue
        result = sts_runner(profile, timeout_sec=sts_timeout_sec)
        if not result.ok:
            stages.append(StageResult(
                "sts_identity", False,
                f"{profile.profile_id}: sts {result.error_kind}: {result.detail}",
            ))
            return ValidationReport(False, tuple(stages), None)
        if result.account_id != profile.expected_account_id:
            stages.append(StageResult(
                "expected_account", False,
                f"{profile.profile_id}: expected "
                f"{mask_account_id(profile.expected_account_id)} but got "
                f"{mask_account_id(result.account_id or '')}",
            ))
            return ValidationReport(False, tuple(stages), None)

    stages.append(StageResult("sts_identity", True, "ok"))
    stages.append(StageResult("expected_account", True, "ok"))
    return ValidationReport(True, tuple(stages), snapshot)
```

在 `multi_profile/__init__.py` 追加：

```python
from .external_validation import StageResult, ValidationReport, run_validation_pipeline

__all__ += ["StageResult", "ValidationReport", "run_validation_pipeline"]
```

- [ ] **步驟 4：執行測試**

```bash
pytest -q tests/test_multi_profile_external_validation.py tests/test_multi_profile_config_loader.py
```

預期：全部 PASS（loader 行為不變）。

- [ ] **步驟 5：提交任務 5**

```bash
git add multi_profile/external_validation.py multi_profile/__init__.py \
  tests/test_multi_profile_external_validation.py
git commit -m "feat(多租戶): 加入發布前外部驗證管線"
```

---

### 任務 6：建立 ConfigPublisher 原子發布與 pending-restart 分類

**文件：**
- 建立：`multi_profile/publisher.py`
- 建立：`tests/test_multi_profile_publisher.py`
- 修改：`multi_profile/__init__.py`

發布流程（規格 §13.4）：驗證 → 暫存檔 → fsync → `os.replace` → registry reload → 只有 snapshot 成功才切換 generation → 存 revision → prune → last-known-good。snapshot 建立失敗時**立即原子恢復上一 revision 本文**，執行中 Registry 保留舊 snapshot。

熱載入範圍（規格 §13.5）：profile 執行欄位、路由、`poll_alerts`、既有 App 的 `default_profile` 可熱載入；新增／刪除 App、`app_id_env`／`app_secret_env` 變更、App enabled 變更為 pending-restart（**可保存**，但必須標示）。

- [ ] **步驟 1：編寫失敗測試**

建立 `tests/test_multi_profile_publisher.py`：

```python
import pytest

from multi_profile.external_validation import StageResult, ValidationReport
from multi_profile.health import ProfileHealthMonitor
from multi_profile.operational_settings import OperationalSettings
from multi_profile.publisher import (
    ChangeSummary,
    ConfigPublisher,
    PublishError,
    classify_changes,
)
from multi_profile.registry import ConfigRegistry
from multi_profile.revisions import RevisionStore, config_checksum


BASE_YAML = """
version: 1
apps:
  ops-bot:
    app_id_env: FEISHU_OPS_APP_ID
    app_secret_env: FEISHU_OPS_APP_SECRET
    default_profile: prod-cn
profiles:
  prod-cn:
    aws_profile: production
    expected_account_id: "123456789012"
    working_dir: {working_dir}
routes: []
"""

ENVIRON = {
    "FEISHU_OPS_APP_ID": "cli_test",
    "FEISHU_OPS_APP_SECRET": "secret_test",
    "FEISHU_EU_APP_ID": "cli_eu",
    "FEISHU_EU_APP_SECRET": "secret_eu",
}


def ok_report(snapshot):
    return ValidationReport(
        True,
        (StageResult("yaml_schema", True, "ok"), StageResult("sts_identity", True, "ok")),
        snapshot,
    )


@pytest.fixture
def stack(tmp_path, monkeypatch):
    config_path = tmp_path / "multi_profile_config.yaml"
    config_path.write_text(BASE_YAML.format(working_dir=tmp_path), encoding="utf-8")
    registry = ConfigRegistry(config_path, environ=ENVIRON)
    registry.load_initial()
    store = RevisionStore(tmp_path / "revs")
    monitor = ProfileHealthMonitor(
        registry.snapshot, settings=OperationalSettings(),
        sts_runner=lambda *a, **kw: None,
    )
    publisher = ConfigPublisher(
        registry=registry,
        revision_store=store,
        health_monitor=monitor,
        validator=lambda text: ok_report(None),
    )
    return publisher, registry, store, monitor, config_path


def test_publish_switches_generation_and_records_revision(stack, tmp_path):
    publisher, registry, store, monitor, config_path = stack
    new_yaml = BASE_YAML.format(working_dir=tmp_path).replace(
        "routes: []",
        "routes:\n  - app: ops-bot\n    chat_id: oc_prod\n    profile: prod-cn\n",
    )

    result = publisher.publish(new_yaml)

    assert result.generation == 2
    assert result.checksum == config_checksum(new_yaml)
    assert registry.snapshot().generation == 2
    assert config_path.read_text(encoding="utf-8") == new_yaml
    assert len(store.list()) == 1
    assert store.list()[0].source == "publish"
    assert (store.directory / "last-known-good.yaml").read_text() == new_yaml
    assert result.change_summary.hot_reloadable
    assert result.change_summary.pending_restart == ()


def test_publish_reruns_validation_and_refuses_invalid_draft(stack):
    publisher, registry, store, _, config_path = stack
    before = config_path.read_text()
    publisher._validator = lambda text: ValidationReport(
        False, (StageResult("sts_identity", False, "timeout"),), None,
    )

    with pytest.raises(PublishError, match="sts_identity"):
        publisher.publish("version: 1\n")

    assert config_path.read_text() == before  # 檔案未被更動
    assert registry.snapshot().generation == 1
    assert store.list() == []


def test_snapshot_failure_restores_previous_revision_atomically(stack, tmp_path):
    """規格 §13.4：os.replace 成功但 snapshot 建立失敗時，恢復上一 revision。"""
    publisher, registry, store, _, config_path = stack
    before = config_path.read_text()

    original_reload = registry.reload

    def failing_reload():
        if config_path.read_text() != before:
            raise RuntimeError("snapshot build failed")
        return original_reload()

    registry.reload = failing_reload
    new_yaml = before + "# touched\n"

    with pytest.raises(PublishError, match="restored previous revision"):
        publisher.publish(new_yaml)

    registry.reload = original_reload
    assert config_path.read_text() == before
    assert registry.snapshot().generation == 1
    assert store.list() == []  # 失敗發布不留 revision


def test_app_credential_env_change_is_pending_restart(stack, tmp_path):
    publisher, registry, store, _, _ = stack
    new_yaml = BASE_YAML.format(working_dir=tmp_path).replace(
        "app_secret_env: FEISHU_OPS_APP_SECRET",
        "app_secret_env: FEISHU_EU_APP_SECRET",
    )

    result = publisher.publish(new_yaml)

    assert result.generation == 2  # 允許保存
    assert result.change_summary.pending_restart == (
        "app ops-bot credential env changed",
    )


def test_app_add_and_remove_are_pending_restart(tmp_path):
    from multi_profile.config_loader import load_config

    old = load_config(
        _write(tmp_path, "old.yaml", BASE_YAML.format(working_dir=tmp_path)),
        environ=ENVIRON, generation=1,
    )
    new_yaml = BASE_YAML.format(working_dir=tmp_path).replace(
        "profiles:",
        "  eu-bot:\n"
        "    app_id_env: FEISHU_EU_APP_ID\n"
        "    app_secret_env: FEISHU_EU_APP_SECRET\n"
        "    default_profile: prod-cn\n"
        "profiles:",
    )
    new = load_config(
        _write(tmp_path, "new.yaml", new_yaml), environ=ENVIRON, generation=2,
    )

    summary = classify_changes(old, new)
    assert "app eu-bot added" in summary.pending_restart

    summary_removed = classify_changes(new, old)
    assert "app eu-bot removed" in summary_removed.pending_restart


def test_route_profile_and_default_profile_changes_are_hot_reloadable(tmp_path):
    from multi_profile.config_loader import load_config

    old = load_config(
        _write(tmp_path, "old.yaml", BASE_YAML.format(working_dir=tmp_path)),
        environ=ENVIRON, generation=1,
    )
    new_yaml = BASE_YAML.format(working_dir=tmp_path).replace(
        "routes: []",
        "routes:\n  - app: ops-bot\n    chat_id: oc_a\n    profile: prod-cn\n"
        "    poll_alerts: true\n",
    ).replace("sync_timeout", "sync_timeout")  # profile 欄位另行覆蓋
    new_yaml = new_yaml.replace(
        "    working_dir:",
        "    sync_timeout: 240\n    working_dir:",
    )
    new = load_config(
        _write(tmp_path, "new.yaml", new_yaml), environ=ENVIRON, generation=2,
    )

    summary = classify_changes(old, new)

    assert summary.pending_restart == ()
    assert "routes changed" in summary.hot_reloadable
    assert "profile prod-cn execution fields changed" in summary.hot_reloadable


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_rollback_revalidates_and_publishes_as_new_revision(stack, tmp_path):
    publisher, registry, store, _, config_path = stack
    first_yaml = config_path.read_text()
    second_yaml = first_yaml.replace(
        "routes: []",
        "routes:\n  - app: ops-bot\n    chat_id: oc_prod\n    profile: prod-cn\n",
    )
    publisher.publish(second_yaml)
    target = store.list()[0].revision_id

    calls = []
    original = publisher._validator
    publisher._validator = lambda text: (calls.append(text), original(text))[1]

    result = publisher.rollback(target)

    assert calls == [first_yaml]  # 回滾內容經過完整重新驗證（含 STS）
    assert result.generation == 3
    assert registry.snapshot().generation == 3
    assert config_path.read_text() == first_yaml
    assert store.list()[-1].source == "rollback"


def test_rollback_validation_failure_keeps_current_snapshot(stack, tmp_path):
    publisher, registry, store, _, config_path = stack
    first_yaml = config_path.read_text()
    publisher.publish(first_yaml + "# v2\n")
    target = store.list()[0].revision_id
    publisher._validator = lambda text: ValidationReport(
        False, (StageResult("expected_account", False, "mismatch"),), None,
    )

    with pytest.raises(PublishError):
        publisher.rollback(target)

    assert registry.snapshot().generation == 2
    assert config_path.read_text() == first_yaml + "# v2\n"


def test_last_results_are_tracked_for_observability(stack, tmp_path):
    publisher, *_ = stack
    publisher.publish(BASE_YAML.format(working_dir=tmp_path) + "# v2\n")

    last = publisher.last_result
    assert last.ok is True
    assert last.action == "publish"
    assert last.error is None
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_publisher.py
```

預期：FAIL，包含 `ModuleNotFoundError: No module named 'multi_profile.publisher'`。

- [ ] **步驟 3：實作 publisher.py**

建立 `multi_profile/publisher.py`：

```python
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .external_validation import ValidationReport, run_validation_pipeline
from .health import ProfileHealthMonitor
from .models import ConfigSnapshot
from .registry import ConfigRegistry
from .revisions import RevisionStore, atomic_write, config_checksum


class PublishError(RuntimeError):
    def __init__(self, message: str, report: ValidationReport | None = None):
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class ChangeSummary:
    hot_reloadable: tuple[str, ...]
    pending_restart: tuple[str, ...]


@dataclass(frozen=True)
class PublishResult:
    generation: int
    checksum: str
    revision_id: str
    change_summary: ChangeSummary


@dataclass(frozen=True)
class LastActionResult:
    action: str  # "load" | "publish" | "rollback"
    ok: bool
    at: str
    error: str | None
    detail: str


def classify_changes(old: ConfigSnapshot, new: ConfigSnapshot) -> ChangeSummary:
    """規格 §13.5：區分可熱載入與需要重啟的變更。"""
    hot: list[str] = []
    restart: list[str] = []

    for app_key in sorted(set(new.apps) - set(old.apps)):
        restart.append(f"app {app_key} added")
    for app_key in sorted(set(old.apps) - set(new.apps)):
        restart.append(f"app {app_key} removed")
    for app_key in sorted(set(old.apps) & set(new.apps)):
        before, after = old.apps[app_key], new.apps[app_key]
        if (before.app_id_env, before.app_secret_env) != (
            after.app_id_env, after.app_secret_env,
        ):
            restart.append(f"app {app_key} credential env changed")
        if before.enabled != after.enabled:
            restart.append(f"app {app_key} enabled changed")
        if before.default_profile != after.default_profile:
            hot.append(f"app {app_key} default_profile changed")

    for pid in sorted(set(new.profiles) | set(old.profiles)):
        if old.profiles.get(pid) != new.profiles.get(pid):
            hot.append(f"profile {pid} execution fields changed")

    if old.routes != new.routes:
        hot.append("routes changed")

    return ChangeSummary(tuple(hot), tuple(restart))


class ConfigPublisher:
    """原子發布、失敗回復與回滾（規格 §13.4、§20.1）。"""

    def __init__(
        self,
        *,
        registry: ConfigRegistry,
        revision_store: RevisionStore,
        health_monitor: ProfileHealthMonitor | None = None,
        validator: Callable[[str], ValidationReport] | None = None,
    ):
        self._registry = registry
        self._store = revision_store
        self._health = health_monitor
        self._validator = validator or (lambda text: run_validation_pipeline(text))
        self._lock = threading.Lock()
        self._last_result = LastActionResult(
            "load", True, _utcnow(), None, "initial load",
        )

    @property
    def last_result(self) -> LastActionResult:
        return self._last_result

    def publish(self, yaml_text: str, *, source: str = "publish") -> PublishResult:
        with self._lock:
            try:
                return self._publish_locked(yaml_text, source=source)
            except PublishError as exc:
                self._last_result = LastActionResult(
                    source, False, _utcnow(), str(exc), "publish failed",
                )
                raise

    def rollback(self, revision_id: str) -> PublishResult:
        """規格 §20.1：歷史內容完整重新驗證（含 STS）後發布為新 revision。"""
        try:
            yaml_text = self._store.read(revision_id)
        except KeyError:
            raise PublishError(f"unknown revision: {revision_id}") from None
        return self.publish(yaml_text, source="rollback")

    def _publish_locked(self, yaml_text: str, *, source: str) -> PublishResult:
        # 1. 伺服器端完整重新驗證（規格 §13.2：不信任瀏覽器結果）
        report = self._validator(yaml_text)
        if not report.ok:
            failed = next(s for s in report.stages if not s.ok)
            raise PublishError(
                f"validation failed at {failed.stage}: {failed.detail}", report,
            )

        config_path = self._registry.path
        old_snapshot = self._registry.snapshot()
        previous_text = config_path.read_text(encoding="utf-8")

        # 2. 原子替換主設定（暫存 → fsync → os.replace）
        atomic_write(config_path, yaml_text)

        # 3. 重建 snapshot；失敗立即恢復上一 revision 本文
        try:
            new_snapshot = self._registry.reload()
        except Exception as exc:
            atomic_write(config_path, previous_text)
            try:
                self._registry.reload()
            except Exception:
                # 連恢復都失敗：保留執行中的舊 snapshot，明確回報
                raise PublishError(
                    f"snapshot build failed ({exc}); restored file but reload "
                    "still failing, runtime keeps previous snapshot"
                ) from exc
            raise PublishError(
                f"snapshot build failed ({exc}); restored previous revision"
            ) from exc

        # 4. snapshot 成功才記錄 revision、prune、更新 last-known-good
        summary = (
            f"{sum(1 for s in report.stages if s.ok)}/{len(report.stages)} stages ok"
        )
        info = self._store.save(
            yaml_text,
            generation=new_snapshot.generation,
            source=source,
            validation_summary=summary,
        )
        self._store.prune()
        self._store.update_last_known_good(yaml_text)

        if self._health is not None:
            self._health.on_config_reload(new_snapshot)

        change_summary = classify_changes(old_snapshot, new_snapshot)
        result = PublishResult(
            generation=new_snapshot.generation,
            checksum=config_checksum(yaml_text),
            revision_id=info.revision_id,
            change_summary=change_summary,
        )
        self._last_result = LastActionResult(
            source, True, _utcnow(), None,
            f"generation {result.generation}, revision {info.revision_id}",
        )
        return result


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
```

在 `multi_profile/__init__.py` 追加：

```python
from .publisher import (
    ChangeSummary,
    ConfigPublisher,
    LastActionResult,
    PublishError,
    PublishResult,
    classify_changes,
)

__all__ += [
    "ChangeSummary",
    "ConfigPublisher",
    "LastActionResult",
    "PublishError",
    "PublishResult",
    "classify_changes",
]
```

- [ ] **步驟 4：執行測試**

```bash
pytest -q tests/test_multi_profile_publisher.py \
  tests/test_multi_profile_registry.py tests/test_multi_profile_health.py
```

預期：全部 PASS。

- [ ] **步驟 5：提交任務 6**

```bash
git add multi_profile/publisher.py multi_profile/__init__.py tests/test_multi_profile_publisher.py
git commit -m "feat(多租戶): 加入原子發布與回滾"
```

---

### 任務 7：建立可觀測性聚合與 TaskRegistry 執行中任務計數

**文件：**
- 建立：`multi_profile/status.py`
- 建立：`tests/test_multi_profile_status.py`
- 修改：`multi_profile/task_registry.py`（additive）
- 修改：`tests/test_multi_profile_task_registry.py`（追加）
- 修改：`multi_profile/__init__.py`

- [ ] **步驟 1：追加 TaskRegistry 計數測試**

在 `tests/test_multi_profile_task_registry.py` 尾端追加：

```python
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
```

- [ ] **步驟 2：編寫 status 聚合失敗測試**

建立 `tests/test_multi_profile_status.py`：

```python
from multi_profile.health import ProfileHealthMonitor
from multi_profile.operational_settings import OperationalSettings
from multi_profile.publisher import LastActionResult
from multi_profile.registry import ConfigRegistry
from multi_profile.revisions import RevisionStore, config_checksum
from multi_profile.status import build_multi_profile_status
from multi_profile.sts import StsResult
from multi_profile.task_registry import TaskRegistry


YAML = """
version: 1
apps:
  ops-bot:
    app_id_env: FEISHU_OPS_APP_ID
    app_secret_env: FEISHU_OPS_APP_SECRET
    default_profile: prod-cn
profiles:
  prod-cn:
    aws_profile: production
    expected_account_id: "123456789012"
    working_dir: {working_dir}
routes: []
"""


class FakeAppManager:
    def app_statuses(self):
        return {"ops-bot": "connected", "eu-bot": "pending-restart"}


def make_stack(tmp_path):
    config_path = tmp_path / "config.yaml"
    text = YAML.format(working_dir=tmp_path)
    config_path.write_text(text, encoding="utf-8")
    registry = ConfigRegistry(config_path, environ={
        "FEISHU_OPS_APP_ID": "cli", "FEISHU_OPS_APP_SECRET": "sec",
    })
    registry.load_initial()
    monitor = ProfileHealthMonitor(
        registry.snapshot,
        settings=OperationalSettings(),
        sts_runner=lambda profile, **kw: StsResult(True, "123456789012", None, "ok"),
    )
    monitor.check_all_now()
    tasks = TaskRegistry()
    tasks.reserve("feishu/ops-bot/group/oc_a/user/ou_1", "prod-cn")
    return registry, monitor, tasks, text


def test_status_matches_spec_section_17(tmp_path):
    registry, monitor, tasks, text = make_stack(tmp_path)

    status = build_multi_profile_status(
        mode="multi-profile",
        registry=registry,
        config_text=text,
        health_monitor=monitor,
        app_manager=FakeAppManager(),
        task_registry=tasks,
        settings=OperationalSettings(),
        last_load=LastActionResult("load", True, "t0", None, "ok"),
        last_publish=LastActionResult("publish", True, "t1", None, "gen 2"),
        last_rollback=None,
    )

    assert status["mode"] == "multi-profile"
    assert status["generation"] == 1
    assert status["checksum"] == config_checksum(text)
    assert status["apps"] == {
        "ops-bot": "connected", "eu-bot": "pending-restart",
    }
    profile = status["profiles"]["prod-cn"]
    assert profile["state"] == "active"
    assert profile["account_id"] == "123456789012"  # Dashboard auth 後方可見完整值
    assert profile["account_id_masked"] == "********9012"
    assert profile["last_sts_at"] is not None
    assert status["tasks"] == {"total": 1, "by_profile": {"prod-cn": 1}}
    assert status["last_load"]["ok"] is True
    assert status["last_publish"]["action"] == "publish"
    assert status["last_rollback"] is None
    assert status["settings"]["health_check_interval_sec"] == 600


def test_legacy_mode_status_is_minimal(tmp_path):
    status = build_multi_profile_status(
        mode="legacy",
        registry=None,
        config_text=None,
        health_monitor=None,
        app_manager=None,
        task_registry=None,
        settings=OperationalSettings(),
        last_load=None,
        last_publish=None,
        last_rollback=None,
    )

    assert status["mode"] == "legacy"
    assert status["generation"] is None
    assert status["profiles"] == {}
    assert status["apps"] == {}
```

- [ ] **步驟 3：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_task_registry.py tests/test_multi_profile_status.py
```

預期：新測試 FAIL（`AttributeError: counts_by_profile` 與 `ModuleNotFoundError: multi_profile.status`）。

- [ ] **步驟 4：TaskRegistry 新增計數方法（additive）**

在 `multi_profile/task_registry.py` 的 `TaskRegistry` 尾端新增（不改動既有方法）：

```python
    def counts_by_profile(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = {}
            for task in self._tasks.values():
                counts[task.profile_id] = counts.get(task.profile_id, 0) + 1
            return counts

    def total_running(self) -> int:
        with self._lock:
            return len(self._tasks)
```

- [ ] **步驟 5：實作 status.py**

建立 `multi_profile/status.py`：

```python
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .health import ProfileHealthMonitor
from .operational_settings import OperationalSettings
from .publisher import LastActionResult
from .registry import ConfigRegistry
from .revisions import config_checksum
from .task_registry import TaskRegistry


def _action_dict(result: LastActionResult | None) -> dict | None:
    return asdict(result) if result is not None else None


def build_multi_profile_status(
    *,
    mode: str,
    registry: ConfigRegistry | None,
    config_text: str | None,
    health_monitor: ProfileHealthMonitor | None,
    app_manager: Any,
    task_registry: TaskRegistry | None,
    settings: OperationalSettings,
    last_load: LastActionResult | None,
    last_publish: LastActionResult | None,
    last_rollback: LastActionResult | None,
) -> dict:
    """規格 §17 可觀測性 payload。此端點僅供 Dashboard 驗證後使用，
    因此 profile 可包含完整 account_id；群內 /profile 仍只用遮罩值。"""
    status: dict[str, Any] = {
        "mode": mode,
        "generation": None,
        "checksum": None,
        "apps": {},
        "profiles": {},
        "tasks": {"total": 0, "by_profile": {}},
        "settings": {
            "sts_timeout_sec": settings.sts_timeout_sec,
            "health_check_interval_sec": settings.health_check_interval_sec,
            "health_grace_sec": settings.health_grace_sec,
            "health_jitter_max_sec": settings.health_jitter_max_sec,
            "revision_keep": settings.revision_keep,
        },
        "last_load": _action_dict(last_load),
        "last_publish": _action_dict(last_publish),
        "last_rollback": _action_dict(last_rollback),
    }

    if mode != "multi-profile" or registry is None:
        return status

    try:
        snapshot = registry.snapshot()
    except RuntimeError:
        return status

    status["generation"] = snapshot.generation
    if config_text is not None:
        status["checksum"] = config_checksum(config_text)

    if app_manager is not None:
        status["apps"] = dict(app_manager.app_statuses())

    if health_monitor is not None:
        for profile_id, health in health_monitor.statuses().items():
            profile = snapshot.profiles.get(profile_id)
            status["profiles"][profile_id] = {
                "state": health.state,
                "account_id": (
                    profile.expected_account_id if profile is not None else None
                ),
                "account_id_masked": health.account_id_masked,
                "last_sts_at": health.last_sts_at,
                "last_error": health.last_error,
                "consecutive_failures": health.consecutive_failures,
            }

    if task_registry is not None:
        status["tasks"] = {
            "total": task_registry.total_running(),
            "by_profile": task_registry.counts_by_profile(),
        }
    return status
```

在 `multi_profile/__init__.py` 追加：

```python
from .status import build_multi_profile_status

__all__ += ["build_multi_profile_status"]
```

- [ ] **步驟 6：執行測試並提交**

```bash
pytest -q tests/test_multi_profile_status.py tests/test_multi_profile_task_registry.py
```

預期：全部 PASS（計畫 2 既有 TaskRegistry 測試不受影響）。

```bash
git add multi_profile/status.py multi_profile/task_registry.py multi_profile/__init__.py \
  tests/test_multi_profile_status.py tests/test_multi_profile_task_registry.py
git commit -m "feat(多租戶): 加入多 profile 可觀測性聚合"
```

---

### 任務 8：建立 Dashboard Multi Profile API

**文件：**
- 建立：`dashboard/multi_profile_api.py`
- 建立：`tests/test_dashboard_api_multi_profile.py`
- 修改：`dashboard/__init__.py`（註冊新模組，比照既有 `import dashboard.api`）

API 邊界（規格 §13.2，全部 `@require_auth`，沿用既有 Cookie Session，不新增驗證機制）：

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/api/dashboard/multi-profile/config` | 目前設定 YAML 本文、解析後 snapshot、pending-restart |
| GET | `/api/dashboard/multi-profile/status` | 規格 §17 可觀測性（generation、checksum、mode、App/profile、任務數、最近 load/publish/rollback） |
| POST | `/api/dashboard/multi-profile/validate` | 驗證 Draft，回傳 §13.3 各階段結果 |
| POST | `/api/dashboard/multi-profile/publish` | 伺服器端重新驗證後原子發布 |
| GET | `/api/dashboard/multi-profile/revisions` | 列出保留的 revision |
| GET | `/api/dashboard/multi-profile/revisions/<revision_id>/diff` | 與目前設定或指定 revision 的 unified diff |
| POST | `/api/dashboard/multi-profile/rollback` | 回滾至指定 revision（完整重新驗證後發布為新 revision） |

設計要點：

- 執行期依賴（registry、publisher、revision store、health monitor、AppManager、TaskRegistry、settings）由 gateway 以 `init_multi_profile_api(MultiProfileDeps(...))` 注入；測試注入 fake／tmp_path 實例，**不在模組 import 時讀取全域狀態**。未注入時所有路由回 503。
- 發布必須由 `ConfigPublisher` 在伺服器端重新執行完整驗證管線（§13.3），不得信任瀏覽器先前的 validate 結果。
- legacy 模式（`MULTI_PROFILE_ENABLED=false`）下 validate 永遠可用；publish 在設定檔尚不存在時走 **bootstrap**：驗證通過後寫檔、建立離線 registry（generation 1）、保存 `source="bootstrap"` revision 與 last-known-good，但不影響 legacy runtime（規格 §19.3 離線設定）。
- API 只處理 YAML（內含環境變數**名稱**）；任何 response 不得包含 Secret 值或完整子程序環境。完整 Account ID 只出現在 `status`（受 Dashboard 驗證保護，規格 §17）；`config` 與 `validate` response 一律只用遮罩值。
- Draft 大小上限 512 KB，超過回 413。

- [ ] **步驟 1：編寫失敗的 API 測試**

建立 `tests/test_dashboard_api_multi_profile.py`：

```python
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask

import dashboard.multi_profile_api as mp_api
from dashboard import dashboard_bp, _sessions
from dashboard.multi_profile_api import MultiProfileDeps, init_multi_profile_api
from multi_profile.external_validation import StageResult, ValidationReport
from multi_profile.operational_settings import OperationalSettings
from multi_profile.publisher import ConfigPublisher
from multi_profile.registry import ConfigRegistry
from multi_profile.revisions import RevisionStore, config_checksum
from multi_profile.sts import StsResult


BASE_YAML = """
version: 1
apps:
  ops-bot:
    app_id_env: FEISHU_OPS_APP_ID
    app_secret_env: FEISHU_OPS_APP_SECRET
    default_profile: prod-cn
profiles:
  prod-cn:
    aws_profile: production
    expected_account_id: "123456789012"
    working_dir: {working_dir}
routes: []
"""

ENVIRON = {
    "FEISHU_OPS_APP_ID": "cli_test",
    "FEISHU_OPS_APP_SECRET": "s3cret-value-must-never-leak",
}


def ok_report(text, snapshot=None):
    return ValidationReport(
        True,
        (StageResult("yaml_schema", True, "ok"), StageResult("sts_identity", True, "ok")),
        snapshot,
    )


@pytest.fixture
def stack(tmp_path):
    config_path = tmp_path / "multi_profile_config.yaml"
    config_path.write_text(BASE_YAML.format(working_dir=tmp_path), encoding="utf-8")
    registry = ConfigRegistry(config_path, environ=ENVIRON)
    registry.load_initial()
    store = RevisionStore(tmp_path / "revs")
    publisher = ConfigPublisher(
        registry=registry, revision_store=store,
        validator=lambda text: ok_report(text),
    )
    deps = MultiProfileDeps(
        mode="multi-profile",
        config_path=config_path,
        revision_dir=tmp_path / "revs",
        registry=registry,
        publisher=publisher,
        revision_store=store,
        settings=OperationalSettings(),
        validator=lambda text: ok_report(text),
    )
    init_multi_profile_api(deps)
    yield deps, config_path, store, publisher
    mp_api.reset_multi_profile_api()


@pytest.fixture
def client(stack):
    _sessions.clear()
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    with app.test_client() as c:
        _sessions["test-session"] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        c.set_cookie("dashboard_session", "test-session")
        yield c
    _sessions.clear()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/dashboard/multi-profile/config"),
        ("GET", "/api/dashboard/multi-profile/status"),
        ("POST", "/api/dashboard/multi-profile/validate"),
        ("POST", "/api/dashboard/multi-profile/publish"),
        ("GET", "/api/dashboard/multi-profile/revisions"),
        ("GET", "/api/dashboard/multi-profile/revisions/rev-x/diff"),
        ("POST", "/api/dashboard/multi-profile/rollback"),
    ],
)
def test_all_routes_require_auth(method, path):
    mp_api.reset_multi_profile_api()
    _sessions.clear()
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    with app.test_client() as c:
        resp = c.open(path, method=method, json={})
        assert resp.status_code == 401
    _sessions.clear()


def test_uninitialized_api_returns_503():
    mp_api.reset_multi_profile_api()
    _sessions.clear()
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    with app.test_client() as c:
        _sessions["s"] = {"created_at": datetime.now(timezone.utc).isoformat()}
        c.set_cookie("dashboard_session", "s")
        resp = c.get("/api/dashboard/multi-profile/config")
        assert resp.status_code == 503
    _sessions.clear()


def test_get_config_returns_text_snapshot_and_never_secrets(client, stack):
    resp = client.get("/api/dashboard/multi-profile/config")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["mode"] == "multi-profile"
    assert "version: 1" in body["config_text"]
    assert body["snapshot"]["apps"]["ops-bot"]["default_profile"] == "prod-cn"
    assert body["pending_restart"] == []
    # env 值絕不出現在 response（只有 env 名稱）
    raw = resp.get_data(as_text=True)
    assert "s3cret-value-must-never-leak" not in raw
    assert "FEISHU_OPS_APP_SECRET" in raw  # 名稱可見，值不可見


def test_validate_returns_pipeline_stages(client, stack):
    deps, *_ = stack
    deps.validator = lambda text: ValidationReport(
        False,
        (
            StageResult("yaml_schema", True, "ok"),
            StageResult("sts_identity", False, "prod-cn: sts timeout"),
        ),
        None,
    )

    resp = client.post(
        "/api/dashboard/multi-profile/validate", json={"yaml": "version: 1\n"},
    )

    assert resp.status_code == 200  # 驗證失敗仍是合法 response
    body = resp.get_json()
    assert body["ok"] is False
    assert [s["stage"] for s in body["stages"]] == ["yaml_schema", "sts_identity"]
    assert body["stages"][1]["ok"] is False


def test_validate_rejects_oversized_draft(client):
    resp = client.post(
        "/api/dashboard/multi-profile/validate",
        json={"yaml": "x" * (513 * 1024)},
    )
    assert resp.status_code == 413


def test_publish_reruns_validation_server_side(client, stack):
    """規格 §13.2：即使瀏覽器宣稱已驗證，伺服器仍完整重新驗證。"""
    deps, config_path, store, _ = stack
    calls = []
    original = deps.publisher._validator
    deps.publisher._validator = lambda text: (calls.append(text), original(text))[1]
    new_yaml = config_path.read_text() + "# v2\n"

    resp = client.post(
        "/api/dashboard/multi-profile/publish",
        json={"yaml": new_yaml, "prevalidated": True},
    )

    assert resp.status_code == 200
    assert calls == [new_yaml]  # 不信任 prevalidated 旗標
    body = resp.get_json()
    assert body["generation"] == 2
    assert body["checksum"] == config_checksum(new_yaml)
    assert body["change_summary"]["pending_restart"] == []


def test_publish_failure_returns_422_and_keeps_file(client, stack):
    deps, config_path, store, _ = stack
    before = config_path.read_text()
    deps.publisher._validator = lambda text: ValidationReport(
        False, (StageResult("expected_account", False, "mismatch"),), None,
    )

    resp = client.post(
        "/api/dashboard/multi-profile/publish", json={"yaml": "version: 1\n"},
    )

    assert resp.status_code == 422
    assert resp.get_json()["ok"] is False
    assert config_path.read_text() == before
    assert store.list() == []


def test_publish_pending_restart_is_surfaced_in_status(client, stack):
    deps, config_path, *_ = stack
    new_yaml = config_path.read_text().replace(
        "app_secret_env: FEISHU_OPS_APP_SECRET",
        "app_secret_env: FEISHU_OPS_APP_SECRET2",
    )
    # 新 env 名稱也要存在，否則 loader 會拒絕
    deps.registry._environ = {**ENVIRON, "FEISHU_OPS_APP_SECRET2": "x"}

    resp = client.post("/api/dashboard/multi-profile/publish", json={"yaml": new_yaml})
    assert resp.status_code == 200
    assert resp.get_json()["change_summary"]["pending_restart"] == [
        "app ops-bot credential env changed",
    ]

    status = client.get("/api/dashboard/multi-profile/status").get_json()
    assert status["pending_restart"] == ["app ops-bot credential env changed"]


def test_revisions_list_and_diff(client, stack):
    deps, config_path, store, _ = stack
    first = config_path.read_text()
    client.post(
        "/api/dashboard/multi-profile/publish",
        json={"yaml": first + "# v2\n"},
    )
    client.post(
        "/api/dashboard/multi-profile/publish",
        json={"yaml": first + "# v2\n# v3\n"},
    )

    listed = client.get("/api/dashboard/multi-profile/revisions").get_json()
    assert listed["ok"] is True
    assert len(listed["revisions"]) == 2
    newest, older = listed["revisions"]  # 最新在前
    assert newest["is_current"] is True
    assert older["is_current"] is False
    assert newest["checksum"] == config_checksum(first + "# v2\n# v3\n")

    resp = client.get(
        f"/api/dashboard/multi-profile/revisions/{older['revision_id']}/diff?against=current",
    )
    assert resp.status_code == 200
    assert "+# v3" in resp.get_json()["diff"]


def test_diff_with_path_traversal_revision_id_is_rejected(client, stack):
    resp = client.get("/api/dashboard/multi-profile/revisions/..%2F..%2F.env/diff")
    assert resp.status_code in (400, 404)
    assert "s3cret" not in resp.get_data(as_text=True)


def test_rollback_revalidates_and_publishes_new_revision(client, stack):
    deps, config_path, store, _ = stack
    first = config_path.read_text()
    second = first.replace(
        "routes: []",
        "routes:\n  - app: ops-bot\n    chat_id: oc_prod\n    profile: prod-cn\n",
    )
    client.post("/api/dashboard/multi-profile/publish", json={"yaml": second})
    # 手動保存 first 為可回滾 revision（publish 只保存新內容）
    store.save(first, generation=1, source="publish", validation_summary="ok")
    target = next(
        r.revision_id for r in store.list() if r.checksum == config_checksum(first)
    )

    calls = []
    original = deps.publisher._validator
    deps.publisher._validator = lambda text: (calls.append(text), original(text))[1]

    resp = client.post(
        "/api/dashboard/multi-profile/rollback", json={"revision_id": target},
    )

    assert resp.status_code == 200
    assert calls == [first]  # 歷史內容經完整重新驗證（含 STS）
    assert config_path.read_text() == first
    assert any(r.source == "rollback" for r in store.list())


def test_rollback_unknown_revision_returns_422(client):
    resp = client.post(
        "/api/dashboard/multi-profile/rollback", json={"revision_id": "no-such"},
    )
    assert resp.status_code == 422


def test_status_payload_matches_spec_17(client, stack):
    resp = client.get("/api/dashboard/multi-profile/status")

    body = resp.get_json()
    assert body["mode"] == "multi-profile"
    assert body["generation"] == 1
    assert len(body["checksum"]) == 64
    assert "settings" in body and body["settings"]["sts_timeout_sec"] == 10
    assert body["tasks"] == {"total": 0, "by_profile": {}}
    assert "last_load" in body and "last_publish" in body and "last_rollback" in body


def test_bootstrap_publish_in_legacy_mode(tmp_path):
    """legacy 模式、設定檔不存在：publish 走 bootstrap，不觸碰 runtime（§19.3）。"""
    config_path = tmp_path / "multi_profile_config.yaml"
    deps = MultiProfileDeps(
        mode="legacy",
        config_path=config_path,
        revision_dir=tmp_path / "revs",
        settings=OperationalSettings(),
        environ=ENVIRON,
        validator=lambda text: ok_report(text),
    )
    init_multi_profile_api(deps)
    try:
        _sessions.clear()
        app = Flask(__name__)
        app.register_blueprint(dashboard_bp)
        with app.test_client() as c:
            _sessions["s"] = {"created_at": datetime.now(timezone.utc).isoformat()}
            c.set_cookie("dashboard_session", "s")
            resp = c.post(
                "/api/dashboard/multi-profile/publish",
                json={"yaml": BASE_YAML.format(working_dir=tmp_path)},
            )
        _sessions.clear()

        assert resp.status_code == 200
        assert resp.get_json()["generation"] == 1
        assert config_path.is_file()
        assert deps.registry is not None and deps.registry.snapshot().generation == 1
        store = RevisionStore(tmp_path / "revs")
        assert store.list()[0].source == "bootstrap"
        assert (tmp_path / "revs" / "last-known-good.yaml").is_file()
    finally:
        mp_api.reset_multi_profile_api()
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_dashboard_api_multi_profile.py
```

預期：FAIL，包含 `ModuleNotFoundError: No module named 'dashboard.multi_profile_api'`。

- [ ] **步驟 3：實作 dashboard/multi_profile_api.py**

建立 `dashboard/multi_profile_api.py`：

```python
#!/usr/bin/env python3
"""Multi Profile Dashboard API（全部 require_auth；規格 §13.2、§17、§20.1）。

執行期依賴由 gateway 以 init_multi_profile_api() 注入；
本模組不保存、不回傳任何 Secret 值。
"""

from __future__ import annotations

import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from flask import jsonify, request

from dashboard import dashboard_bp, require_auth
from multi_profile import (
    ConfigPublisher,
    ConfigRegistry,
    ConfigSnapshot,
    LastActionResult,
    OperationalSettings,
    PublishError,
    RevisionStore,
    atomic_write,
    build_multi_profile_status,
    config_checksum,
    run_validation_pipeline,
)

MAX_DRAFT_BYTES = 512 * 1024


@dataclass
class MultiProfileDeps:
    mode: str  # "multi-profile" | "legacy"
    config_path: Path
    revision_dir: Path
    registry: ConfigRegistry | None = None
    publisher: ConfigPublisher | None = None
    revision_store: RevisionStore | None = None
    health_monitor: Any = None
    app_manager: Any = None
    task_registry: Any = None
    settings: OperationalSettings = field(default_factory=OperationalSettings)
    environ: Mapping[str, str] | None = None
    validator: Callable[[str], Any] = run_validation_pipeline
    last_load: LastActionResult | None = None


_deps: MultiProfileDeps | None = None
_lock = threading.Lock()
_pending_restart: tuple[str, ...] = ()
_last_publish: LastActionResult | None = None
_last_rollback: LastActionResult | None = None


def init_multi_profile_api(deps: MultiProfileDeps) -> None:
    global _deps, _pending_restart, _last_publish, _last_rollback
    with _lock:
        _deps = deps
        _pending_restart = ()
        _last_publish = None
        _last_rollback = None


def reset_multi_profile_api() -> None:
    """測試用：清除注入的依賴與模組狀態。"""
    global _deps, _pending_restart, _last_publish, _last_rollback
    with _lock:
        _deps = None
        _pending_restart = ()
        _last_publish = None
        _last_rollback = None


def _require_deps() -> MultiProfileDeps | None:
    return _deps


def _unavailable():
    return jsonify({"ok": False, "error": "multi-profile api is not initialized"}), 503


def _read_draft() -> tuple[str | None, Any]:
    payload = request.get_json(silent=True) or {}
    yaml_text = payload.get("yaml")
    if not isinstance(yaml_text, str) or not yaml_text.strip():
        return None, (jsonify({"ok": False, "error": "yaml is required"}), 400)
    if len(yaml_text.encode("utf-8")) > MAX_DRAFT_BYTES:
        return None, (jsonify({"ok": False, "error": "draft too large"}), 413)
    return yaml_text, None


def _snapshot_dict(snapshot: ConfigSnapshot) -> dict:
    return {
        "version": snapshot.version,
        "generation": snapshot.generation,
        "apps": {key: asdict(app) for key, app in snapshot.apps.items()},
        "profiles": {
            key: asdict(profile) for key, profile in snapshot.profiles.items()
        },
        "routes": [asdict(route) for route in snapshot.routes],
    }


def _status_payload(deps: MultiProfileDeps) -> dict:
    config_text = None
    if deps.config_path.is_file():
        config_text = deps.config_path.read_text(encoding="utf-8")
    status = build_multi_profile_status(
        mode=deps.mode,
        registry=deps.registry,
        config_text=config_text,
        health_monitor=deps.health_monitor,
        app_manager=deps.app_manager,
        task_registry=deps.task_registry,
        settings=deps.settings,
        last_load=deps.last_load,
        last_publish=_last_publish,
        last_rollback=_last_rollback,
    )
    status["pending_restart"] = list(_pending_restart)
    return status


def _record_result(source: str, result: LastActionResult) -> None:
    global _last_publish, _last_rollback
    if source == "rollback":
        _last_rollback = result
    else:
        _last_publish = result


def _bootstrap(deps: MultiProfileDeps, yaml_text: str) -> dict:
    """legacy 模式首次發布（規格 §19.3）：建立離線 registry，不切換 runtime。"""
    report = deps.validator(yaml_text)
    if not report.ok:
        failed = next(s for s in report.stages if not s.ok)
        raise PublishError(
            f"validation failed at {failed.stage}: {failed.detail}", report,
        )
    atomic_write(deps.config_path, yaml_text)
    registry = ConfigRegistry(
        deps.config_path,
        environ=deps.environ if deps.environ is not None else os.environ,
    )
    snapshot = registry.load_initial()
    store = RevisionStore(deps.revision_dir)
    summary = f"{sum(1 for s in report.stages if s.ok)}/{len(report.stages)} stages ok"
    info = store.save(
        yaml_text, generation=snapshot.generation,
        source="bootstrap", validation_summary=summary,
    )
    store.prune(deps.settings.revision_keep)
    store.update_last_known_good(yaml_text)
    publisher = ConfigPublisher(
        registry=registry,
        revision_store=store,
        health_monitor=deps.health_monitor,
        validator=deps.validator,
    )
    deps.registry = registry
    deps.revision_store = store
    deps.publisher = publisher
    return {
        "generation": snapshot.generation,
        "checksum": config_checksum(yaml_text),
        "revision_id": info.revision_id,
        "change_summary": {"hot_reloadable": [], "pending_restart": []},
    }


@dashboard_bp.route("/api/dashboard/multi-profile/config", methods=["GET"])
@require_auth
def get_multi_profile_config():
    deps = _require_deps()
    if deps is None:
        return _unavailable()
    body = {
        "ok": True,
        "mode": deps.mode,
        "config_path": str(deps.config_path),
        "exists": deps.config_path.is_file(),
        "config_text": None,
        "snapshot": None,
        "pending_restart": list(_pending_restart),
    }
    if deps.config_path.is_file():
        body["config_text"] = deps.config_path.read_text(encoding="utf-8")
    if deps.registry is not None:
        try:
            body["snapshot"] = _snapshot_dict(deps.registry.snapshot())
        except RuntimeError:
            pass
    return jsonify(body)


@dashboard_bp.route("/api/dashboard/multi-profile/status", methods=["GET"])
@require_auth
def get_multi_profile_status():
    deps = _require_deps()
    if deps is None:
        return _unavailable()
    payload = _status_payload(deps)
    payload["ok"] = True
    return jsonify(payload)


@dashboard_bp.route("/api/dashboard/multi-profile/validate", methods=["POST"])
@require_auth
def validate_multi_profile_draft():
    deps = _require_deps()
    if deps is None:
        return _unavailable()
    yaml_text, error = _read_draft()
    if error:
        return error
    report = deps.validator(yaml_text)
    return jsonify({
        "ok": report.ok,
        "stages": [asdict(stage) for stage in report.stages],
    })


@dashboard_bp.route("/api/dashboard/multi-profile/publish", methods=["POST"])
@require_auth
def publish_multi_profile_draft():
    global _pending_restart
    deps = _require_deps()
    if deps is None:
        return _unavailable()
    yaml_text, error = _read_draft()
    if error:
        return error
    try:
        if deps.publisher is None:
            result = _bootstrap(deps, yaml_text)
        else:
            published = deps.publisher.publish(yaml_text)
            _pending_restart = published.change_summary.pending_restart
            result = {
                "generation": published.generation,
                "checksum": published.checksum,
                "revision_id": published.revision_id,
                "change_summary": {
                    "hot_reloadable": list(published.change_summary.hot_reloadable),
                    "pending_restart": list(published.change_summary.pending_restart),
                },
            }
        _record_result("publish", deps.publisher.last_result)
    except PublishError as exc:
        body = {"ok": False, "error": str(exc)}
        if exc.report is not None:
            body["stages"] = [asdict(stage) for stage in exc.report.stages]
        return jsonify(body), 422
    result["ok"] = True
    return jsonify(result)


@dashboard_bp.route("/api/dashboard/multi-profile/revisions", methods=["GET"])
@require_auth
def list_multi_profile_revisions():
    deps = _require_deps()
    if deps is None:
        return _unavailable()
    store = deps.revision_store or RevisionStore(deps.revision_dir)
    current = None
    if deps.config_path.is_file():
        current = config_checksum(deps.config_path.read_text(encoding="utf-8"))
    revisions = []
    for info in reversed(store.list()):
        item = asdict(info)
        item["is_current"] = info.checksum == current
        revisions.append(item)
    return jsonify({"ok": True, "revisions": revisions})


@dashboard_bp.route(
    "/api/dashboard/multi-profile/revisions/<revision_id>/diff", methods=["GET"],
)
@require_auth
def diff_multi_profile_revision(revision_id):
    deps = _require_deps()
    if deps is None:
        return _unavailable()
    store = deps.revision_store or RevisionStore(deps.revision_dir)
    against = request.args.get("against", "current")
    try:
        if against == "current":
            current_text = (
                deps.config_path.read_text(encoding="utf-8")
                if deps.config_path.is_file() else ""
            )
            diff = store.diff(revision_id, against_text=current_text)
        else:
            diff = store.diff(revision_id, against_revision=against)
    except (KeyError, ValueError):
        return jsonify({"ok": False, "error": "unknown revision"}), 404
    return jsonify({"ok": True, "diff": diff})


@dashboard_bp.route("/api/dashboard/multi-profile/rollback", methods=["POST"])
@require_auth
def rollback_multi_profile():
    global _pending_restart
    deps = _require_deps()
    if deps is None or deps.publisher is None:
        return _unavailable()
    payload = request.get_json(silent=True) or {}
    revision_id = payload.get("revision_id", "")
    try:
        published = deps.publisher.rollback(revision_id)
    except PublishError as exc:
        body = {"ok": False, "error": str(exc)}
        if exc.report is not None:
            body["stages"] = [asdict(stage) for stage in exc.report.stages]
        return jsonify(body), 422
    _pending_restart = published.change_summary.pending_restart
    _record_result("rollback", deps.publisher.last_result)
    return jsonify({
        "ok": True,
        "generation": published.generation,
        "checksum": published.checksum,
        "revision_id": published.revision_id,
        "change_summary": {
            "hot_reloadable": list(published.change_summary.hot_reloadable),
            "pending_restart": list(published.change_summary.pending_restart),
        },
    })
```

在 `dashboard/__init__.py` 尾端的 `import dashboard.api  # noqa: F401` 之後追加：

```python
import dashboard.multi_profile_api  # noqa: F401
```

- [ ] **步驟 4：執行測試**

```bash
pytest -q tests/test_dashboard_api_multi_profile.py tests/test_dashboard_auth.py
```

預期：全部 PASS（既有 auth 測試不受影響）。

- [ ] **步驟 5：確認既有 Dashboard API 回歸**

```bash
pytest -q tests/test_config_store.py tests/test_dashboard_api_resources.py \
  tests/test_dashboard_api_events.py
```

預期：全部 PASS；`dashboard/api.py` 與 `dashboard/config_store.py` 零修改。

- [ ] **步驟 6：提交任務 8**

```bash
git add dashboard/multi_profile_api.py dashboard/__init__.py \
  tests/test_dashboard_api_multi_profile.py
git commit -m "feat(多租戶): 加入 Multi Profile Dashboard API"
```

---

### 任務 9：Dashboard 前端 Multi Profile Config 頁面

**文件：**
- 修改：`dashboard/static/app.js`（新增 `MultiProfilePage`、`/multi-profile` 路由、導航項目）
- 修改：`dashboard/static/style.css`（少量樣式：YAML 編輯器、階段結果、diff 檢視）

頁面結構（規格 §13.1）：**Apps**、**Profiles**、**Group Routes**、**Revisions** 四個區塊，加上 Draft 編輯器與狀態列。本專案前端無自動化測試基礎（`dashboard/static/` 無 JS 測試），本任務以手動驗證步驟收口；所有行為由任務 8 的 API 測試保證。

安全邊界（規格 §13.1、§16）：

- Apps 區塊只編輯 `app_id_env`／`app_secret_env` 的**環境變數名稱**，頁面不提供任何 Secret 值輸入欄位。
- Account ID 在 Dashboard（已驗證）可顯示完整值；遮罩值格式 `********9012` 僅用於 `/profile` 群內指令，不在本頁重複實作。

- [ ] **步驟 1：新增 MultiProfilePage 元件**

在 `dashboard/static/app.js` 的 `ConfigPage` 定義之後新增（沿用既有 `api()` helper 與 `.card`／`.toolbar`／`.table-wrap`／`.badge`／`.modal-*` CSS 類別）：

```javascript
const MultiProfilePage = {
  template: `
    <div>
      <div class="toolbar">
        <h2>Multi Profile Config</h2>
        <span class="badge">{{ status.mode || 'legacy' }}</span>
        <span class="badge" v-if="status.generation">gen {{ status.generation }}</span>
        <button class="btn-outline" @click="loadAll" :disabled="loading">重新整理</button>
      </div>

      <div v-if="pendingRestart.length" class="card" style="border-left:4px solid var(--pale-yellow)">
        <strong>pending-restart：</strong>以下變更已保存，需重啟服務才生效
        <ul><li v-for="item in pendingRestart" :key="item">{{ item }}</li></ul>
      </div>

      <div class="card">
        <h3>Apps</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>別名</th><th>app_id_env</th><th>app_secret_env</th><th>預設 profile</th><th>連線狀態</th></tr></thead>
          <tbody><tr v-for="(app, key) in apps" :key="key">
            <td>{{ key }}</td><td><code>{{ app.app_id_env }}</code></td>
            <td><code>{{ app.app_secret_env }}</code></td>
            <td>{{ app.default_profile }}</td>
            <td><span class="badge">{{ appStatus(key) }}</span></td>
          </tr></tbody>
        </table></div>
      </div>

      <div class="card">
        <h3>Profiles</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>profile</th><th>AWS profile</th><th>Region</th><th>Account</th><th>Agent / Model</th><th>working_dir</th><th>健康</th><th>最近 STS</th></tr></thead>
          <tbody><tr v-for="(p, key) in profiles" :key="key">
            <td>{{ key }}</td><td>{{ p.aws_profile }}</td>
            <td>{{ p.aws_region || 'profile default' }}</td>
            <td><code>{{ p.expected_account_id }}</code></td>
            <td>{{ p.kiro_agent || 'default' }} / {{ p.model || 'default' }}</td>
            <td><code>{{ p.working_dir }}</code></td>
            <td><span class="badge">{{ healthState(key) }}</span></td>
            <td>{{ healthTime(key) }}</td>
          </tr></tbody>
        </table></div>
      </div>

      <div class="card">
        <h3>Group Routes</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>App</th><th>chat_id</th><th>profile</th><th>poll_alerts</th></tr></thead>
          <tbody><tr v-for="(r, i) in routes" :key="i">
            <td>{{ r.app_key }}</td><td><code>{{ r.chat_id }}</code></td>
            <td>{{ r.profile_id }}</td><td>{{ r.poll_alerts }}</td>
          </tr></tbody>
        </table></div>
      </div>

      <div class="card">
        <h3>Draft 編輯（只含 env 變數名稱，不含 Secret 值）</h3>
        <textarea v-model="draft" class="mp-yaml-editor" rows="18" spellcheck="false"></textarea>
        <div class="toolbar">
          <button class="btn-outline" @click="validateDraft" :disabled="busy">驗證 Draft</button>
          <button class="btn-outline" @click="publishDraft" :disabled="busy || !validationOk">發布</button>
        </div>
        <div v-if="stages.length" class="mp-stages">
          <div v-for="s in stages" :key="s.stage" class="mp-stage" :class="s.ok ? 'ok' : 'fail'">
            {{ s.ok ? '✓' : '✗' }} {{ s.stage }} — {{ s.detail }}
          </div>
        </div>
        <div v-if="publishResult" class="card">
          已發布 generation {{ publishResult.generation }}（revision {{ publishResult.revision_id }}）
          <div v-if="publishResult.change_summary.pending_restart.length">
            pending-restart：{{ publishResult.change_summary.pending_restart.join('；') }}
          </div>
        </div>
      </div>

      <div class="card">
        <h3>Revisions（保留最近 {{ revisions.length }} 筆）</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>時間</th><th>generation</th><th>checksum</th><th>來源</th><th>驗證</th><th>操作</th></tr></thead>
          <tbody><tr v-for="r in revisions" :key="r.revision_id">
            <td>{{ r.created_at }}</td><td>{{ r.generation }}</td>
            <td><code>{{ r.checksum.slice(0, 8) }}</code>
              <span v-if="r.is_current" class="badge">current</span></td>
            <td>{{ r.source }}</td><td>{{ r.validation_summary }}</td>
            <td>
              <button class="btn-sm btn-outline" @click="showDiff(r)">diff</button>
              <button class="btn-sm btn-outline" @click="rollback(r)" :disabled="busy">回滾</button>
            </td>
          </tr></tbody>
        </table></div>
      </div>

      <div class="modal-overlay" v-if="diffText !== null" @click.self="diffText = null">
        <div class="modal" style="max-width:860px">
          <div class="modal-header"><h3>Revision diff</h3></div>
          <div class="modal-body"><pre class="mp-diff">{{ diffText }}</pre></div>
          <div class="modal-footer">
            <button class="btn-outline" @click="diffText = null">關閉</button>
          </div>
        </div>
      </div>

      <div v-if="error" class="card" style="color:var(--pale-red-text)">{{ error }}</div>
    </div>
  `,
  setup() {
    const { ref, computed, onMounted } = Vue;
    const loading = ref(false), busy = ref(false), error = ref("");
    const snapshot = ref(null), status = ref({}), draft = ref("");
    const stages = ref([]), publishResult = ref(null);
    const revisions = ref([]), diffText = ref(null);

    const apps = computed(() => snapshot.value?.apps || {});
    const profiles = computed(() => snapshot.value?.profiles || {});
    const routes = computed(() => snapshot.value?.routes || []);
    const pendingRestart = computed(() => status.value.pending_restart || []);
    const validationOk = computed(
      () => stages.value.length > 0 && stages.value.every((s) => s.ok),
    );

    const appStatus = (key) => status.value.apps?.[key] || "unknown";
    const healthState = (key) => status.value.profiles?.[key]?.state || "unknown";
    const healthTime = (key) => {
      const ts = status.value.profiles?.[key]?.last_sts_at;
      return ts ? new Date(ts * 1000).toLocaleString() : "—";
    };

    async function loadAll() {
      loading.value = true; error.value = "";
      try {
        const cfg = await api("/multi-profile/config");
        snapshot.value = cfg.snapshot;
        draft.value = cfg.config_text || "";
        status.value = await api("/multi-profile/status");
        const revs = await api("/multi-profile/revisions");
        revisions.value = revs.revisions;
      } catch (e) { error.value = String(e); }
      finally { loading.value = false; }
    }
    async function validateDraft() {
      busy.value = true; error.value = ""; publishResult.value = null;
      try {
        const resp = await api("/multi-profile/validate", {
          method: "POST", body: { yaml: draft.value },
        });
        stages.value = resp.stages;
      } catch (e) { error.value = String(e); }
      finally { busy.value = false; }
    }
    async function publishDraft() {
      if (!confirm("確定發布？伺服器端會重新執行完整驗證（含 STS）。")) return;
      busy.value = true; error.value = "";
      try {
        publishResult.value = await api("/multi-profile/publish", {
          method: "POST", body: { yaml: draft.value },
        });
        await loadAll();
      } catch (e) { error.value = String(e); }
      finally { busy.value = false; }
    }
    async function showDiff(r) {
      const resp = await api(
        `/multi-profile/revisions/${encodeURIComponent(r.revision_id)}/diff?against=current`,
      );
      diffText.value = resp.diff || "(無差異)";
    }
    async function rollback(r) {
      if (!confirm(`回滾至 ${r.revision_id}？歷史內容會重新驗證（含 STS）後發布為新 revision。`)) return;
      busy.value = true; error.value = "";
      try {
        await api("/multi-profile/rollback", {
          method: "POST", body: { revision_id: r.revision_id },
        });
        await loadAll();
      } catch (e) { error.value = String(e); }
      finally { busy.value = false; }
    }
    onMounted(loadAll);

    return {
      loading, busy, error, snapshot, status, draft, stages, publishResult,
      revisions, diffText, apps, profiles, routes, pendingRestart, validationOk,
      appStatus, healthState, healthTime,
      loadAll, validateDraft, publishDraft, showDiff, rollback,
    };
  },
};
```

- [ ] **步驟 2：註冊路由與導航**

在 `routes` 陣列的 `/config` 之後加入：

```javascript
  { path: "/multi-profile", component: MultiProfilePage },
```

在 sidebar `<nav>` 的 `<router-link to="/config">Config</router-link>` 之後加入：

```html
          <router-link to="/multi-profile">Multi Profile</router-link>
```

- [ ] **步驟 3：新增樣式**

在 `dashboard/static/style.css` 尾端追加：

```css
.mp-yaml-editor {
  width: 100%;
  font-family: var(--font-mono, monospace);
  font-size: 13px;
  background: var(--bg-muted, #f8f9fa);
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px;
  padding: 12px;
}
.mp-stage { padding: 4px 8px; font-size: 13px; }
.mp-stage.ok { color: var(--pale-green-text, #15803d); }
.mp-stage.fail { color: var(--pale-red-text, #b91c1c); }
.mp-diff {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  white-space: pre-wrap;
  max-height: 480px;
  overflow: auto;
}
```

- [ ] **步驟 4：手動驗證（無 JS 測試基礎，此為本任務收口）**

```bash
# 以現有 gateway/dashboard 啟動方式啟動服務後：
# 1. 登入 Dashboard → sidebar 出現 Multi Profile，進入 /multi-profile
# 2. Apps/Profiles/Group Routes 顯示與 multi_profile_config.yaml 一致；無任何 Secret 值
# 3. 修改 Draft（如加一條 route）→ 驗證 Draft 顯示 8 階段全 ✓ → 發布 → generation +1
# 4. 把 expected_account_id 改成錯誤值 → 驗證在 expected_account 階段 ✗，發布按鈕不可點
# 5. 改 app_secret_env 名稱 → 發布成功但頂部出現 pending-restart 提示
# 6. Revisions 列表出現新 revision → diff 顯示與目前差異 → 回滾後 generation 再 +1
# 7. 瀏覽器 DevTools Network 檢查所有 /multi-profile/* response：不含 FEISHU_*_APP_SECRET 的值
```

- [ ] **步驟 5：提交任務 9**

```bash
git add dashboard/static/app.js dashboard/static/style.css
git commit -m "feat(多租戶): 加入 Multi Profile Config 頁面"
```

---

### 任務 10：gateway 接線健康監控、blocked 閘門與日誌衛生

**文件：**
- 修改：`gateway.py`（multi-profile 分支啟動健康監控、注入 Dashboard 依賴、訊息／告警入口 blocked 拒絕）
- 建立：`tests/test_multi_profile_log_hygiene.py`
- 修改：`tests/test_dashboard_api_multi_profile.py`（若接線後 import 順序需要調整）

計畫 3 已將 `gateway.py` 重構為 `build_gateway` 並依 feature flag 分 legacy／multi-profile 兩條接線路徑；以下接點名稱以計畫 3 為準，本任務只加 additive 邏輯，不改變 legacy 路徑。

- [ ] **步驟 1：編寫日誌衛生失敗測試**

建立 `tests/test_multi_profile_log_hygiene.py`：

```python
import json
import logging

import pytest

from multi_profile.external_validation import run_validation_pipeline
from multi_profile.health import ProfileHealthMonitor
from multi_profile.models import AppConfig, ProfileConfig, create_snapshot
from multi_profile.operational_settings import OperationalSettings
from multi_profile.sts import StsResult, mask_account_id


SECRET_VALUES = [
    "s3cret-feishu-value",
    "AKIAIOSFODNN7EXAMPLE",
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
]


def make_snapshot():
    apps = {
        "ops-bot": AppConfig(
            app_key="ops-bot",
            app_id_env="FEISHU_OPS_APP_ID",
            app_secret_env="FEISHU_OPS_APP_SECRET",
            default_profile="prod-cn",
        )
    }
    profiles = {
        "prod-cn": ProfileConfig(
            profile_id="prod-cn",
            aws_profile="production",
            expected_account_id="123456789012",
            working_dir="/srv/kiro-devops",
        )
    }
    return create_snapshot(1, apps, profiles, ())


def all_log_text(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def test_validation_failure_logs_contain_no_secret_or_full_account(caplog, tmp_path):
    yaml_text = """
version: 1
apps:
  ops-bot:
    app_id_env: FEISHU_OPS_APP_ID
    app_secret_env: FEISHU_OPS_APP_SECRET
    default_profile: prod-cn
profiles:
  prod-cn:
    aws_profile: production
    expected_account_id: "123456789012"
    working_dir: {working_dir}
routes: []
""".format(working_dir=tmp_path)
    environ = {
        "FEISHU_OPS_APP_ID": "cli_test",
        "FEISHU_OPS_APP_SECRET": SECRET_VALUES[0],
    }

    with caplog.at_level(logging.DEBUG):
        report = run_validation_pipeline(
            yaml_text,
            environ=environ,
            kiro_agents_dir=tmp_path / "no-agents",
            aws_config_dir=tmp_path / "no-aws",
            model_lister=lambda: [],
            sts_runner=lambda profile, **kw: StsResult(
                True, "999999999999", None, "ok",
            ),
        )

    assert report.ok is False
    combined = all_log_text(caplog) + json.dumps(
        [s.__dict__ for s in report.stages],
    )
    for secret in SECRET_VALUES:
        assert secret not in combined
    # Account ID 只以遮罩形式出現
    assert "999999999999" not in combined
    if "********" in combined:
        assert "********9999" in combined


def test_health_monitor_logs_contain_no_full_account(caplog):
    snapshot = make_snapshot()

    def sts_runner(profile, **kw):
        return StsResult(True, "999999999999", None, "ok")

    monitor = ProfileHealthMonitor(
        lambda: snapshot,
        settings=OperationalSettings(),
        sts_runner=sts_runner,
    )
    with caplog.at_level(logging.DEBUG):
        monitor.check_all_now()

    assert "999999999999" not in all_log_text(caplog)
    assert monitor.health("prod-cn").account_id_masked == "********9999"


def test_mask_account_id_format_is_stable():
    assert mask_account_id("123456789012") == "********9012"
    assert len(mask_account_id("123456789012")) == 12


def test_health_monitor_never_logs_child_environment(caplog, monkeypatch):
    """規格 §16：不得輸出完整子程序環境。"""
    snapshot = make_snapshot()
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", SECRET_VALUES[2])

    captured = {}

    def sts_runner(profile, **kw):
        return StsResult(True, "123456789012", None, "ok")

    monitor = ProfileHealthMonitor(
        lambda: snapshot, settings=OperationalSettings(), sts_runner=sts_runner,
    )
    with caplog.at_level(logging.DEBUG):
        monitor.check_all_now()

    for secret in SECRET_VALUES:
        assert secret not in all_log_text(caplog)
```

- [ ] **步驟 2：執行測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_log_hygiene.py
```

預期：FAIL；若 PASS 代表既有實作已符合衛生要求，仍保留測試作為迴歸保護。

- [ ] **步驟 3：gateway multi-profile 分支接線**

在計畫 3 建立的 multi-profile 接線分支中（`MULTI_PROFILE_ENABLED=true` 路徑），於 registry 載入成功後加入：

```python
from multi_profile import (
    ConfigPublisher,
    OperationalSettings,
    ProfileHealthMonitor,
    RevisionStore,
    load_operational_settings,
    revision_dir_from_env,
)
from dashboard.multi_profile_api import MultiProfileDeps, init_multi_profile_api

settings = load_operational_settings()  # 值越界時啟動即失敗，帶明確錯誤
health_monitor = ProfileHealthMonitor(registry.snapshot, settings=settings)
health_monitor.check_all_now()   # 啟動時先檢查一輪，避免樂觀窗口過長
health_monitor.start()           # daemon 執行緒；interval + jitter

revision_store = RevisionStore(
    revision_dir_from_env(os.environ, project_dir=PROJECT_DIR),
)
publisher = ConfigPublisher(
    registry=registry,
    revision_store=revision_store,
    health_monitor=health_monitor,
)
init_multi_profile_api(MultiProfileDeps(
    mode="multi-profile",
    config_path=registry.path,
    revision_dir=revision_store.directory,
    registry=registry,
    publisher=publisher,
    revision_store=revision_store,
    health_monitor=health_monitor,
    app_manager=app_manager,       # 計畫 3 建立
    task_registry=task_registry,   # 計畫 2 建立
    settings=settings,
))
```

legacy 分支（`MULTI_PROFILE_ENABLED=false`）也要注入**離線**依賴，讓 Dashboard 可驗證與 bootstrap Draft（規格 §19.3），但不建立 health monitor、不啟動任何 runtime：

```python
init_multi_profile_api(MultiProfileDeps(
    mode="legacy",
    config_path=config_path(os.environ, project_dir=PROJECT_DIR),
    revision_dir=revision_dir_from_env(os.environ, project_dir=PROJECT_DIR),
    settings=load_operational_settings(),
))
```

服務關閉路徑加入 `health_monitor.stop()`（multi-profile 分支）。

- [ ] **步驟 4：訊息與告警入口加入 blocked 閘門**

在計畫 3 的 multi-profile 訊息入口（`TenantRouter.resolve()` 取得 `ExecutionContext` 之後、`TaskRegistry.reserve()` 之前）插入：

```python
try:
    health_monitor.ensure_usable(context.profile_id)
except ProfileUnavailable as exc:
    # 原 App、原群明確拒絕；不啟動 Kiro／AWS 子程序，不建議替代 profile
    dispatcher.get_adapter("feishu", message.app_key).reply_text(
        message,
        f"⚠️ 此對話綁定的 profile `{context.profile_id}` 目前不可用"
        f"（{exc}），已拒絕新任務。請聯絡管理員檢查 AWS 設定。",
    )
    return
```

群告警分析入口（計畫 3 的告警 ExecutionContext 建立之後）加入同樣閘門；拒絕文案改為告警版本。實際 reply 方法名以計畫 3 的 Adapter／Dispatcher 介面為準；重點：**blocked/disabled profile 的新普通聊天與群告警任務都必須拒絕**（規格 §14），且 Health Monitor 永不自動切換 profile。

- [ ] **步驟 5：執行測試與相關回歸**

```bash
pytest -q tests/test_multi_profile_log_hygiene.py \
  tests/test_multi_profile_health.py \
  tests/test_dashboard_api_multi_profile.py \
  tests/test_dashboard_auth.py
```

預期：全部 PASS。

- [ ] **步驟 6：確認 legacy gateway 行為不變**

```bash
PLAN4_BASE_SHA=$(cat .git/plan4-base-sha)
git diff "${PLAN4_BASE_SHA}"..HEAD -- gateway.py | grep -E "^[-+]" | grep -v "multi_profile\|MultiProfile\|health_monitor\|ProfileUnavailable\|init_multi_profile" || true
pytest -q tests/test_multi_profile_gateway*.py 2>/dev/null || pytest -q -k gateway
```

預期：diff 過濾後無遺漏的非 additive 修改；gateway 相關測試全綠。

- [ ] **步驟 7：提交任務 10**

```bash
git add gateway.py tests/test_multi_profile_log_hygiene.py
git commit -m "feat(多租戶): 接線健康監控與 blocked 閘門"
```

---

### 任務 11：計畫級完整驗證

**文件：**
- 不新增檔案；只驗證任務 1–10 的結果。

- [ ] **步驟 1：執行計畫 4 targeted tests**

```bash
pytest -q \
  tests/test_multi_profile_sts.py \
  tests/test_multi_profile_operational_settings.py \
  tests/test_multi_profile_health.py \
  tests/test_multi_profile_revisions.py \
  tests/test_multi_profile_external_validation.py \
  tests/test_multi_profile_publisher.py \
  tests/test_multi_profile_status.py \
  tests/test_dashboard_api_multi_profile.py \
  tests/test_multi_profile_log_hygiene.py
```

預期：全部 PASS，0 failed。

- [ ] **步驟 2：執行計畫 1–4 全部 multi-profile 測試**

```bash
pytest -q tests/test_multi_profile_*.py
```

預期：全部 PASS（計畫 1–3 既有測試不受 additive 修改影響）。

- [ ] **步驟 3：執行 Dashboard 與 legacy 回歸**

```bash
pytest -q \
  tests/test_dashboard_auth.py \
  tests/test_config_store.py \
  tests/test_dashboard_api_resources.py \
  tests/test_dashboard_api_resources_history.py \
  tests/test_dashboard_api_resources_tencent.py \
  tests/test_dashboard_api_events.py \
  tests/test_dashboard_api_resource_tree.py \
  tests/test_platform_dispatcher.py \
  tests/test_group_alert_detection.py
```

預期：全部 PASS；既有 Dashboard token 驗證、.env 編輯、資源查詢行為不變。

- [ ] **步驟 4：執行完整測試套件**

```bash
pytest -q
```

預期：0 failed。若出現既有基線失敗，停止並依 systematic-debugging 確認，不得忽略。

- [ ] **步驟 5：執行 Python 編譯檢查**

```bash
python3 -m compileall -q multi_profile dashboard tests
```

預期：exit 0。

- [ ] **步驟 6：確認 runtime/ 被忽略且無 Secret 進入版本控制**

```bash
git check-ignore -v runtime/config-revisions/multi-profile/last-known-good.yaml
git status --short -- runtime/ | head
grep -rn "app_secret_value\|aws_secret" multi_profile_config.example.yaml multi_profile/*.py dashboard/multi_profile_api.py || true
```

預期：第一行顯示 `.gitignore` 命中；第二行無輸出（runtime/ 不被追蹤）；第三行無實際 Secret 欄位。

- [ ] **步驟 7：確認未修改凍結介面與 legacy 路徑**

```bash
PLAN4_BASE_SHA=$(cat .git/plan4-base-sha)
git diff "${PLAN4_BASE_SHA}"..HEAD -- \
  multi_profile/models.py multi_profile/config_loader.py multi_profile/router.py \
  dashboard/api.py dashboard/config_store.py \
  kiro_executor.py session_router.py alert_analysis.py adapters platform_dispatcher.py
```

預期：沒有輸出（計畫 1 介面凍結；legacy executor／session／alert／adapter 不變）。

- [ ] **步驟 8：確認公開介面可由計畫 5 匯入**

```bash
python3 - <<'PY'
from multi_profile import (
    ConfigPublisher,
    LastActionResult,
    OperationalSettings,
    ProfileHealth,
    ProfileHealthMonitor,
    ProfileUnavailable,
    PublishError,
    RevisionInfo,
    RevisionStore,
    StageResult,
    StsResult,
    ValidationReport,
    atomic_write,
    build_multi_profile_status,
    classify_changes,
    config_checksum,
    load_operational_settings,
    mask_account_id,
    revision_dir_from_env,
    run_sts_check,
    run_validation_pipeline,
)
from dashboard.multi_profile_api import MultiProfileDeps, init_multi_profile_api
print("plan 4 public API import OK")
PY
```

預期：輸出 `plan 4 public API import OK`。

- [ ] **步驟 9：確認提交與工作區**

```bash
git status --short
PLAN4_BASE_SHA=$(cat .git/plan4-base-sha)
git log --oneline "${PLAN4_BASE_SHA}"..HEAD
```

預期：沒有未提交的計畫 4 檔案；列出的範圍只包含計畫 4 實作與其審查修正提交。

---

## 完成標準

- ProfileHealthMonitor 以 `AWS_STS_TIMEOUT_SEC`（3–60，預設 10）、`PROFILE_HEALTH_CHECK_INTERVAL_SEC`（60–3600，預設 600）＋ 0–60 秒 jitter、`PROFILE_HEALTH_GRACE_SEC`（0–86400，預設 1800）執行週期性隔離 STS 檢查；永不修改 `os.environ`。
- 狀態機完整：active／degraded／blocked／disabled；Account ID 不符與 profile 不存在**立即 blocked**（無 grace）；暫時性失敗超過 grace 才 blocked；Monitor 永不自動切換 profile。
- blocked／disabled profile 的新普通聊天與群告警任務在原 App、原群明確拒絕，不啟動任何子程序。
- 驗證管線依規格 §13.3 固定順序短路：YAML/schema → env refs → 關聯完整 → 路徑逾時 → Kiro Agent／模型 → AWS CLI profile → 隔離 STS → `expected_account_id`；任何一步失敗都不得發布。
- 原子發布依規格 §13.4：暫存檔 → fsync → `os.replace` → 目錄 fsync → snapshot 成功才切換 generation；snapshot 失敗立即恢復上一 revision 本文，執行中 Registry 保留舊 snapshot。
- Revision 保留最近 20 份於 `MULTI_PROFILE_REVISION_DIR`（預設 `runtime/config-revisions/multi-profile/`），含 checksum、時間、來源與驗證摘要；last-known-good 原子更新；`runtime/` 被 gitignore。
- 熱載入分類正確：profile 執行欄位／路由／`poll_alerts`／既有 App `default_profile` 可熱載入；App 增刪、credential env 變更、App enabled 變更顯示 `pending-restart`。
- 設定回滾（規格 §20.1）：先看 diff → 選歷史 revision → 完整重新驗證（含 STS）→ 發布為新 revision；驗證或發布失敗不切換目前 snapshot。
- Dashboard API 全部沿用既有 token Cookie 驗證；發布在伺服器端重新驗證，不信任瀏覽器結果；任何 response 不含 Secret 值或完整子程序環境。
- 可觀測性端點（規格 §17）回傳 generation、checksum、mode、App 狀態、profile 健康與遮罩 Account ID、任務數、最近 load/publish/rollback 結果與 pending-restart。
- Targeted、計畫 1–4、Dashboard legacy、完整 pytest 與 compileall 全部通過。

## 不在本計畫範圍

- 不啟用 `MULTI_PROFILE_ENABLED=true`；正式切流只允許在計畫 5。
- 不修改計畫 1 凍結介面（`models.py`／`config_loader.py`／`router.py`）與計畫 3 的 AppManager／Dispatcher 實作。
- 不實作 `/profile` 群內指令本身（計畫 3 範圍）；本計畫只提供其所需的健康狀態查詢介面。
- 不做 release manifest、dark deployment、legacy-default 切換、應用版本回滾工具與規模測試（計畫 5）。
- 不在 YAML、revision、API response 或日誌中保存任何 Secret 值；Dashboard 永遠只編輯環境變數名稱。
- 不為 Dashboard 前端新增 JS 測試框架；前端以手動驗證步驟收口，行為由 API 層測試保證。

## 回滾注意事項

- **代碼回滾：** 本計畫全部為 additive。以 `git rev-parse HEAD`（執行前基線記錄於 `.git/plan4-base-sha`）為回滾點，`git reset --hard <plan4-base-sha>` 即可回到計畫 3 完成狀態；legacy 路徑全程不受影響。
- **設定回滾（執行期）：** 不需要動代碼。Dashboard「Revisions」區塊或 `ConfigPublisher.rollback(<revision_id>)` 會把歷史內容完整重新驗證（含 STS）後發布為新 revision；驗證失敗不自動切換，目前 snapshot 保持不變。緊急時可直接以 `runtime/config-revisions/multi-profile/last-known-good.yaml` 覆蓋主設定並重啟。
- **資料相容性：** `runtime/config-revisions/multi-profile/` 下的 revision 與 last-known-good 都是 additive 新檔；回滾代碼後舊版會忽略它們，不需要刪除。`runtime/` 已被 gitignore，不會誤進版本控制。
- **服務降級：** 若健康監控或 Dashboard API 造成問題，設 `MULTI_PROFILE_ENABLED=false` 並重啟即回到 legacy 單 App 模式；legacy 分支下本計畫只注入離線 Dashboard 依賴，不啟動健康監控執行緒。
- **pending-restart 狀態：** 只存於程序記憶體；重啟後自然歸零（此時 pending 變更已生效），不需額外清理。

## 發布驗收對照（規格 §22）

| §22 項目 | 本計畫覆蓋方式 |
|----------|----------------|
| 1. 完整 pytest 零失敗 | 任務 11 步驟 4 |
| 2. Python 編譯檢查通過 | 任務 11 步驟 5 |
| 3. Legacy smoke test | 計畫 5；本計畫以 `tests/test_dashboard_auth.py` 等回歸保證不破壞 |
| 4. 雙 AWS profile 真實 STS 端到端 | 計畫 5；本計畫提供 `run_sts_check`／驗證管線供其呼叫 |
| 9. 無效熱載入不影響目前 snapshot | `test_failed_reload_keeps_previous_snapshot`（計畫 1）、`test_snapshot_failure_restores_previous_revision_atomically`、`test_publish_failure_returns_422_and_keeps_file` |
| 10. 設定 revision 回滾成功 | `test_rollback_revalidates_and_publishes_as_new_revision`、`test_rollback_revalidates_and_publishes_new_revision`（API 層） |
| 12. 日誌與 Dashboard response 不含 Secret／credential | `tests/test_multi_profile_log_hygiene.py`、`test_get_config_returns_text_snapshot_and_never_secrets`、任務 9 手動驗證步驟 7 |
| §21.3 Dashboard 與回滾測試 | STS 成功才可發布（publisher 測試）、STS timeout／profile 不存在／Account 不符（external_validation 測試）、寫檔／snapshot 失敗保留舊設定（publisher 測試）、revision diff／重新驗證／回滾（API 測試）、App 連線欄位變更 pending-restart（classify_changes＋API 測試）、日誌掃描（log hygiene 測試） |
| 其餘 §22 項目（5–8、11） | 由計畫 2／3／5 覆蓋，不在本計畫 |

## 計畫 5 可依賴的公開介面

```python
from multi_profile import (
    ConfigPublisher,
    LastActionResult,
    OperationalSettings,
    ProfileHealth,
    ProfileHealthMonitor,
    ProfileUnavailable,
    PublishError,
    RevisionInfo,
    RevisionStore,
    StageResult,
    StsResult,
    ValidationReport,
    atomic_write,
    build_multi_profile_status,
    classify_changes,
    config_checksum,
    load_operational_settings,
    mask_account_id,
    revision_dir_from_env,
    run_sts_check,
    run_validation_pipeline,
)
from dashboard.multi_profile_api import MultiProfileDeps, init_multi_profile_api
```

計畫 5 必須使用 `ConfigPublisher`／`RevisionStore` 執行離線設定與回滾演練，使用 `run_validation_pipeline` 做切流前最終驗證，並透過 `build_multi_profile_status` 確認 generation 與 pending-restart 狀態；不得繞過驗證管線直接寫入 `multi_profile_config.yaml`。
