# 多 Profile 設定模型與路由核心實作計畫

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推薦）或 superpowers:executing-plans 逐任務實現此計畫。步驟使用復選框（`- [ ]`）語法追蹤進度。

**目標：** 在不改變現有 gateway 行為的前提下，建立多飛書 App／群與 AWS profile 後續功能共用的不可變設定模型、嚴格 YAML 載入器、ConfigRegistry、TenantRouter 與 feature flag API。

**架構：** 新增獨立 `multi_profile` package，將設定解析、不可變資料模型、snapshot generation 與路由決策隔離。此計畫不把新核心接到 `gateway.py`；`MULTI_PROFILE_ENABLED` 預設為 false，正式切流只允許在總體規格的計畫 5 進行。

**技術棧：** Python 3.10+、標準庫 `dataclasses`／`hashlib`／`threading`／`types`、PyYAML、pytest。

**參考規格：** `docs/superpowers/specs/2026-07-14-multi-profile-multi-feishu-group-design.md`

---

## 檔案結構

### 建立

- `multi_profile/__init__.py`：只匯出計畫 2–4 可依賴的公開介面。
- `multi_profile/models.py`：不可變 App、Profile、Route、ConfigSnapshot 與 ExecutionContext 模型；計算 profile fingerprint。
- `multi_profile/config_loader.py`：嚴格載入 YAML、套用預設值並完成 schema、env、關聯與路徑驗證。
- `multi_profile/registry.py`：執行緒安全地保存目前 snapshot；reload 失敗時保留舊 generation。
- `multi_profile/router.py`：群與私聊路由、principal／group scope 產生及 ExecutionContext 建立。
- `multi_profile/feature_flags.py`：解析 `MULTI_PROFILE_ENABLED` 與設定檔路徑，不啟動新 runtime。
- `tests/test_multi_profile_models.py`：模型不可變性、snapshot 防外部修改、fingerprint 行為。
- `tests/test_multi_profile_config_loader.py`：Schema、預設值、未知欄位、env、關聯及路徑驗證。
- `tests/test_multi_profile_registry.py`：generation 與 reload fail-safe。
- `tests/test_multi_profile_router.py`：群、私聊、未映射與隔離鍵。
- `tests/test_multi_profile_feature_flags.py`：預設關閉與環境值解析。
- `multi_profile_config.example.yaml`：不含 Secret 的完整範例。

### 修改

- `.env.example`：新增 feature flag 與設定檔路徑註解，預設不啟用。
- `.gitignore`：忽略 `runtime/` 與本機 active `multi_profile_config.yaml`，保留範例檔。

### 明確不修改

- `gateway.py`
- `message_handler.py`
- `kiro_executor.py`
- `session_router.py`
- `alert_analysis.py`
- `platform_dispatcher.py`
- `adapters/`
- `dashboard/`

---

### 任務 1：建立不可變設定模型與 fingerprint

**文件：**
- 建立：`multi_profile/__init__.py`
- 建立：`multi_profile/models.py`
- 建立：`tests/test_multi_profile_models.py`

- [ ] **步驟 1：編寫失敗的模型測試**

建立 `tests/test_multi_profile_models.py`：

```python
from dataclasses import FrozenInstanceError, replace

import pytest

from multi_profile.models import (
    AppConfig,
    ConfigSnapshot,
    ExecutionContext,
    ProfileConfig,
    RouteConfig,
    build_profile_fingerprint,
    create_snapshot,
)


def test_profile_defaults_match_schema():
    profile = ProfileConfig(
        profile_id="prod-cn",
        aws_profile="production",
        expected_account_id="123456789012",
        working_dir="/srv/kiro-devops",
    )

    assert profile.enabled is True
    assert profile.aws_region is None
    assert profile.kiro_agent is None
    assert profile.model is None
    assert profile.alert_agent == "ec2-alert-analyzer"
    assert profile.alert_model is None
    assert profile.sync_timeout == 120
    assert profile.async_timeout == 1800
    assert profile.alert_timeout == 300


def test_models_are_frozen():
    app = AppConfig(
        app_key="ops-bot",
        app_id_env="FEISHU_OPS_APP_ID",
        app_secret_env="FEISHU_OPS_APP_SECRET",
        default_profile="prod-cn",
    )

    with pytest.raises(FrozenInstanceError):
        app.default_profile = "other"


def test_snapshot_copies_and_protects_mappings():
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
    routes = (
        RouteConfig(
            app_key="ops-bot",
            chat_id="oc_prod",
            profile_id="prod-cn",
        ),
    )

    snapshot = create_snapshot(1, apps, profiles, routes)
    apps.clear()

    assert tuple(snapshot.apps) == ("ops-bot",)
    with pytest.raises(TypeError):
        snapshot.apps["other"] = snapshot.apps["ops-bot"]


def test_fingerprint_changes_only_for_session_sensitive_fields():
    profile = ProfileConfig(
        profile_id="prod-cn",
        aws_profile="production",
        aws_region="cn-northwest-1",
        expected_account_id="123456789012",
        kiro_agent="my-dev-bot",
        model="claude-sonnet",
        working_dir="/srv/kiro-devops",
    )
    original = build_profile_fingerprint(profile)

    assert build_profile_fingerprint(replace(profile, sync_timeout=240)) == original
    assert build_profile_fingerprint(replace(profile, aws_region="cn-north-1")) != original
    assert build_profile_fingerprint(replace(profile, kiro_agent="other")) != original


def test_execution_context_is_frozen():
    profile = ProfileConfig(
        profile_id="prod-cn",
        aws_profile="production",
        expected_account_id="123456789012",
        working_dir="/srv/kiro-devops",
    )
    context = ExecutionContext(
        config_generation=1,
        platform="feishu",
        app_key="ops-bot",
        chat_type="group",
        chat_id="oc_prod",
        user_id="ou_user",
        principal_key="feishu/ops-bot/group/oc_prod/user/ou_user",
        group_scope_key="feishu/ops-bot/group/oc_prod",
        profile_id="prod-cn",
        profile=profile,
        profile_fingerprint=build_profile_fingerprint(profile),
    )

    with pytest.raises(FrozenInstanceError):
        context.profile_id = "other"
```

- [ ] **步驟 2：執行測試並確認因 package 不存在而失敗**

執行：

```bash
pytest -q tests/test_multi_profile_models.py
```

預期：FAIL，包含 `ModuleNotFoundError: No module named 'multi_profile'`。

- [ ] **步驟 3：建立最少且完整的模型實作**

建立 `multi_profile/models.py`：

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class AppConfig:
    app_key: str
    app_id_env: str
    app_secret_env: str
    default_profile: str
    enabled: bool = True


@dataclass(frozen=True)
class ProfileConfig:
    profile_id: str
    aws_profile: str
    expected_account_id: str
    working_dir: str
    enabled: bool = True
    aws_region: str | None = None
    kiro_agent: str | None = None
    model: str | None = None
    alert_agent: str = "ec2-alert-analyzer"
    alert_model: str | None = None
    sync_timeout: int = 120
    async_timeout: int = 1800
    alert_timeout: int = 300


@dataclass(frozen=True)
class RouteConfig:
    app_key: str
    chat_id: str
    profile_id: str
    poll_alerts: bool = False


@dataclass(frozen=True)
class ConfigSnapshot:
    version: int
    generation: int
    apps: Mapping[str, AppConfig]
    profiles: Mapping[str, ProfileConfig]
    routes: tuple[RouteConfig, ...]


@dataclass(frozen=True)
class ExecutionContext:
    config_generation: int
    platform: str
    app_key: str
    chat_type: str
    chat_id: str | None
    user_id: str
    principal_key: str
    group_scope_key: str | None
    profile_id: str
    profile: ProfileConfig
    profile_fingerprint: str


def build_profile_fingerprint(profile: ProfileConfig) -> str:
    payload = {
        "profile_id": profile.profile_id,
        "aws_profile": profile.aws_profile,
        "aws_region": profile.aws_region,
        "kiro_agent": profile.kiro_agent,
        "model": profile.model,
        "working_dir": profile.working_dir,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_snapshot(
    generation: int,
    apps: Mapping[str, AppConfig],
    profiles: Mapping[str, ProfileConfig],
    routes: tuple[RouteConfig, ...],
) -> ConfigSnapshot:
    return ConfigSnapshot(
        version=1,
        generation=generation,
        apps=MappingProxyType(dict(apps)),
        profiles=MappingProxyType(dict(profiles)),
        routes=tuple(routes),
    )
```

建立 `multi_profile/__init__.py`：

```python
from .models import (
    AppConfig,
    ConfigSnapshot,
    ExecutionContext,
    ProfileConfig,
    RouteConfig,
    build_profile_fingerprint,
    create_snapshot,
)

__all__ = [
    "AppConfig",
    "ConfigSnapshot",
    "ExecutionContext",
    "ProfileConfig",
    "RouteConfig",
    "build_profile_fingerprint",
    "create_snapshot",
]
```

- [ ] **步驟 4：重新執行模型測試**

執行：

```bash
pytest -q tests/test_multi_profile_models.py
```

預期：5 passed。

- [ ] **步驟 5：提交任務 1**

```bash
git add multi_profile/__init__.py multi_profile/models.py tests/test_multi_profile_models.py
git commit -m "feat(多租戶): 定義不可變執行設定模型"
```

---

### 任務 2：載入有效 YAML 並套用預設值

**文件：**
- 建立：`multi_profile/config_loader.py`
- 建立：`tests/test_multi_profile_config_loader.py`
- 修改：`multi_profile/__init__.py`

- [ ] **步驟 1：編寫有效設定與預設值測試**

建立 `tests/test_multi_profile_config_loader.py`：

```python
from pathlib import Path

import pytest

from multi_profile.config_loader import ConfigError, load_config


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
    expected_account_id: "123456789012"
    working_dir: {working_dir}
routes:
  - app: ops-bot
    chat_id: oc_prod
    profile: prod-cn
"""


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "multi_profile_config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def valid_env() -> dict[str, str]:
    return {
        "FEISHU_OPS_APP_ID": "cli_test",
        "FEISHU_OPS_APP_SECRET": "secret_test",
    }


def test_load_config_applies_defaults(tmp_path):
    path = write_config(tmp_path, VALID_YAML.format(working_dir=tmp_path))

    snapshot = load_config(path, environ=valid_env(), generation=7)

    assert snapshot.version == 1
    assert snapshot.generation == 7
    assert snapshot.apps["ops-bot"].enabled is True
    assert snapshot.profiles["prod-cn"].aws_region is None
    assert snapshot.profiles["prod-cn"].alert_agent == "ec2-alert-analyzer"
    assert snapshot.profiles["prod-cn"].sync_timeout == 120
    assert snapshot.routes[0].poll_alerts is False


def test_explicit_empty_environment_does_not_use_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("FEISHU_OPS_APP_ID", "host_app")
    monkeypatch.setenv("FEISHU_OPS_APP_SECRET", "host_secret")
    path = write_config(tmp_path, VALID_YAML.format(working_dir=tmp_path))

    with pytest.raises(ConfigError, match="missing env FEISHU_OPS_APP_ID"):
        load_config(path, environ={})


def test_load_config_rejects_unknown_fields(tmp_path):
    text = VALID_YAML.format(working_dir=tmp_path).replace(
        "    default_profile: prod-cn",
        "    default_profile: prod-cn\n    app_secret_value: forbidden",
    )
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError, match="apps.ops-bot.*unknown field.*app_secret_value"):
        load_config(path, environ=valid_env())


def test_load_config_reports_yaml_errors(tmp_path):
    path = write_config(tmp_path, "version: [")

    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path, environ={})


def test_rejects_boolean_version(tmp_path):
    text = VALID_YAML.format(working_dir=tmp_path).replace("version: 1", "version: true")
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError, match="config.version must be integer 1"):
        load_config(path, environ=valid_env())


@pytest.mark.parametrize(
    ("text_transform", "message"),
    [
        (lambda text: text.replace("  ops-bot:", "  123:", 1), "apps keys must be strings"),
        (lambda text: text.replace("  prod-cn:", "  123:", 1), "profiles keys must be strings"),
        (
            lambda text: text.replace(
                "    default_profile: prod-cn",
                "    default_profile: prod-cn\n    123: invalid",
            ),
            "apps.123 keys must be strings|apps.ops-bot keys must be strings",
        ),
        (lambda text: text + "\n123: invalid\n", "config keys must be strings"),
    ],
)
def test_rejects_non_string_schema_keys(tmp_path, text_transform, message):
    text = text_transform(VALID_YAML.format(working_dir=tmp_path))
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError, match=message):
        load_config(path, environ=valid_env())
```

- [ ] **步驟 2：執行測試並確認 loader 尚不存在**

執行：

```bash
pytest -q tests/test_multi_profile_config_loader.py
```

預期：FAIL，包含 `ModuleNotFoundError: No module named 'multi_profile.config_loader'`。

- [ ] **步驟 3：實作嚴格欄位解析與預設值**

建立 `multi_profile/config_loader.py`：

```python
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

import yaml

from .models import AppConfig, ProfileConfig, RouteConfig, create_snapshot


class ConfigError(ValueError):
    pass


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ROOT_FIELDS = {"version", "apps", "profiles", "routes"}
_APP_FIELDS = {"enabled", "app_id_env", "app_secret_env", "default_profile"}
_PROFILE_FIELDS = {
    "enabled",
    "aws_profile",
    "aws_region",
    "expected_account_id",
    "kiro_agent",
    "model",
    "alert_agent",
    "alert_model",
    "working_dir",
    "sync_timeout",
    "async_timeout",
    "alert_timeout",
}
_ROUTE_FIELDS = {"app", "chat_id", "profile", "poll_alerts"}


def _mapping(value, path: str) -> dict:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} must be a mapping")
    data = dict(value)
    if any(not isinstance(key, str) for key in data):
        raise ConfigError(f"{path} keys must be strings")
    return data


def _list(value, path: str) -> list:
    if not isinstance(value, list):
        raise ConfigError(f"{path} must be a list")
    return value


def _reject_unknown(data: dict, allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(f"{path}: unknown field(s): {', '.join(unknown)}")


def _required_string(data: dict, field: str, path: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}.{field} must be a non-empty string")
    return value.strip()


def _optional_string(data: dict, field: str, path: str) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}.{field} must be null or a non-empty string")
    return value.strip()


def _boolean(data: dict, field: str, default: bool, path: str) -> bool:
    value = data.get(field, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{path}.{field} must be a boolean")
    return value


def _integer(data: dict, field: str, default: int, path: str) -> int:
    value = data.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path}.{field} must be an integer")
    return value


def _parse_app(app_key: str, value) -> AppConfig:
    path = f"apps.{app_key}"
    data = _mapping(value, path)
    _reject_unknown(data, _APP_FIELDS, path)
    return AppConfig(
        app_key=app_key,
        enabled=_boolean(data, "enabled", True, path),
        app_id_env=_required_string(data, "app_id_env", path),
        app_secret_env=_required_string(data, "app_secret_env", path),
        default_profile=_required_string(data, "default_profile", path),
    )


def _parse_profile(profile_id: str, value) -> ProfileConfig:
    path = f"profiles.{profile_id}"
    data = _mapping(value, path)
    _reject_unknown(data, _PROFILE_FIELDS, path)
    return ProfileConfig(
        profile_id=profile_id,
        enabled=_boolean(data, "enabled", True, path),
        aws_profile=_required_string(data, "aws_profile", path),
        aws_region=_optional_string(data, "aws_region", path),
        expected_account_id=_required_string(data, "expected_account_id", path),
        kiro_agent=_optional_string(data, "kiro_agent", path),
        model=_optional_string(data, "model", path),
        alert_agent=_optional_string(data, "alert_agent", path) or "ec2-alert-analyzer",
        alert_model=_optional_string(data, "alert_model", path),
        working_dir=_required_string(data, "working_dir", path),
        sync_timeout=_integer(data, "sync_timeout", 120, path),
        async_timeout=_integer(data, "async_timeout", 1800, path),
        alert_timeout=_integer(data, "alert_timeout", 300, path),
    )


def _parse_route(index: int, value) -> RouteConfig:
    path = f"routes[{index}]"
    data = _mapping(value, path)
    _reject_unknown(data, _ROUTE_FIELDS, path)
    return RouteConfig(
        app_key=_required_string(data, "app", path),
        chat_id=_required_string(data, "chat_id", path),
        profile_id=_required_string(data, "profile", path),
        poll_alerts=_boolean(data, "poll_alerts", False, path),
    )


def load_config(
    path: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
    generation: int = 1,
):
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc

    root = _mapping(raw, "config")
    _reject_unknown(root, _ROOT_FIELDS, "config")
    version = root.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ConfigError("config.version must be integer 1")

    raw_apps = _mapping(root.get("apps"), "apps")
    raw_profiles = _mapping(root.get("profiles"), "profiles")
    raw_routes = _list(root.get("routes"), "routes")
    if not raw_apps:
        raise ConfigError("apps must not be empty")
    if not raw_profiles:
        raise ConfigError("profiles must not be empty")

    apps = {key: _parse_app(key, value) for key, value in raw_apps.items()}
    profiles = {key: _parse_profile(key, value) for key, value in raw_profiles.items()}
    routes = tuple(_parse_route(i, value) for i, value in enumerate(raw_routes))

    _validate_config(
        apps,
        profiles,
        routes,
        environ if environ is not None else os.environ,
    )
    return create_snapshot(generation, apps, profiles, routes)


def _validate_config(apps, profiles, routes, environ: Mapping[str, str]) -> None:
    for app_key in apps:
        if not _ID_RE.fullmatch(app_key):
            raise ConfigError(f"invalid app key: {app_key}")
    for profile_id in profiles:
        if not _ID_RE.fullmatch(profile_id):
            raise ConfigError(f"invalid profile id: {profile_id}")
```

- [ ] **步驟 4：暫時加入最少驗證尾端，使 happy path 可執行**

在 `_validate_config` 尾端加入：

```python
    for app in apps.values():
        for field_name, env_name in (
            ("app_id_env", app.app_id_env),
            ("app_secret_env", app.app_secret_env),
        ):
            if not _ENV_RE.fullmatch(env_name):
                raise ConfigError(f"apps.{app.app_key}.{field_name} has invalid env name")
            if app.enabled and not environ.get(env_name, "").strip():
                raise ConfigError(f"apps.{app.app_key}.{field_name} references missing env {env_name}")
```

在 `multi_profile/__init__.py` 追加匯出：

```python
from .config_loader import ConfigError, load_config

__all__ += ["ConfigError", "load_config"]
```

- [ ] **步驟 5：執行目前 loader 測試**

```bash
pytest -q tests/test_multi_profile_config_loader.py
```

預期：9 passed。

- [ ] **步驟 6：提交任務 2**

```bash
git add multi_profile/config_loader.py multi_profile/__init__.py tests/test_multi_profile_config_loader.py
git commit -m "feat(多租戶): 載入嚴格 YAML 設定"
```

---

### 任務 3：完成跨欄位、時間與路徑驗證

**文件：**
- 修改：`multi_profile/config_loader.py`
- 修改：`tests/test_multi_profile_config_loader.py`

- [ ] **步驟 1：追加失敗的驗證測試**

在 `tests/test_multi_profile_config_loader.py` 追加：

```python
@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("expected_account_id: \"123456789012\"", "expected_account_id: \"123\"", "expected_account_id"),
        ("sync_timeout: 120", "sync_timeout: 9", "sync_timeout"),
        ("async_timeout: 1800", "async_timeout: 60", "async_timeout"),
        ("alert_timeout: 300", "alert_timeout: 3601", "alert_timeout"),
    ],
)
def test_rejects_invalid_account_and_timeout_values(tmp_path, old, new, message):
    base = VALID_YAML.format(working_dir=tmp_path).replace(
        "    working_dir:",
        "    sync_timeout: 120\n    async_timeout: 1800\n    alert_timeout: 300\n    working_dir:",
    )
    path = write_config(tmp_path, base.replace(old, new))

    with pytest.raises(ConfigError, match=message):
        load_config(path, environ=valid_env())


def test_rejects_missing_working_directory(tmp_path):
    path = write_config(
        tmp_path,
        VALID_YAML.format(working_dir=tmp_path / "missing"),
    )

    with pytest.raises(ConfigError, match="working_dir.*existing directory"):
        load_config(path, environ=valid_env())


def test_rejects_duplicate_routes(tmp_path):
    text = VALID_YAML.format(working_dir=tmp_path) + """
  - app: ops-bot
    chat_id: oc_prod
    profile: prod-cn
"""
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError, match="duplicate route.*ops-bot.*oc_prod"):
        load_config(path, environ=valid_env())


def test_rejects_missing_route_profile(tmp_path):
    text = VALID_YAML.format(working_dir=tmp_path).replace(
        "    profile: prod-cn",
        "    profile: missing",
    )
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError, match="routes.*missing profile"):
        load_config(path, environ=valid_env())


def test_rejects_disabled_default_profile(tmp_path):
    text = VALID_YAML.format(working_dir=tmp_path).replace(
        "  prod-cn:\n",
        "  prod-cn:\n    enabled: false\n",
    )
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError, match="default_profile.*disabled"):
        load_config(path, environ=valid_env())


def test_disabled_app_still_requires_enabled_default_profile(tmp_path):
    text = VALID_YAML.format(working_dir=tmp_path)
    text = text.replace(
        "  ops-bot:\n",
        "  ops-bot:\n    enabled: false\n",
    ).replace(
        "  prod-cn:\n",
        "  prod-cn:\n    enabled: false\n",
    )
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError, match="default_profile.*disabled"):
        load_config(path, environ=valid_env())
```

- [ ] **步驟 2：執行新增測試並確認失敗**

```bash
pytest -q tests/test_multi_profile_config_loader.py
```

預期：至少 5 個新增案例 FAIL，原因分別為尚未驗證 account、timeout、路徑、重複路由與參照。

- [ ] **步驟 3：以完整驗證取代 `_validate_config`**

將 `multi_profile/config_loader.py` 的 `_validate_config` 完整替換為：

```python
def _validate_config(apps, profiles, routes, environ: Mapping[str, str]) -> None:
    for app_key in apps:
        if not _ID_RE.fullmatch(app_key):
            raise ConfigError(f"invalid app key: {app_key}")
    for profile_id in profiles:
        if not _ID_RE.fullmatch(profile_id):
            raise ConfigError(f"invalid profile id: {profile_id}")

    for app in apps.values():
        for field_name, env_name in (
            ("app_id_env", app.app_id_env),
            ("app_secret_env", app.app_secret_env),
        ):
            if not _ENV_RE.fullmatch(env_name):
                raise ConfigError(f"apps.{app.app_key}.{field_name} has invalid env name")
            if app.enabled and not environ.get(env_name, "").strip():
                raise ConfigError(f"apps.{app.app_key}.{field_name} references missing env {env_name}")

        target = profiles.get(app.default_profile)
        if target is None:
            raise ConfigError(
                f"apps.{app.app_key}.default_profile references missing profile {app.default_profile}"
            )
        if not target.enabled:
            raise ConfigError(
                f"apps.{app.app_key}.default_profile references disabled profile {app.default_profile}"
            )

    for profile in profiles.values():
        path = f"profiles.{profile.profile_id}"
        if not re.fullmatch(r"\d{12}", profile.expected_account_id):
            raise ConfigError(f"{path}.expected_account_id must be 12 digits")
        working_dir = Path(profile.working_dir)
        if not working_dir.is_absolute() or not working_dir.is_dir():
            raise ConfigError(f"{path}.working_dir must be an existing absolute directory")
        if not os.access(working_dir, os.R_OK | os.X_OK):
            raise ConfigError(f"{path}.working_dir is not readable by the service user")
        if not 10 <= profile.sync_timeout <= 600:
            raise ConfigError(f"{path}.sync_timeout must be between 10 and 600")
        if not profile.sync_timeout <= profile.async_timeout <= 86400:
            raise ConfigError(
                f"{path}.async_timeout must be between sync_timeout and 86400"
            )
        if not 10 <= profile.alert_timeout <= 3600:
            raise ConfigError(f"{path}.alert_timeout must be between 10 and 3600")

    seen_routes: set[tuple[str, str]] = set()
    for index, route in enumerate(routes):
        key = (route.app_key, route.chat_id)
        if key in seen_routes:
            raise ConfigError(f"duplicate route for {route.app_key} {route.chat_id}")
        seen_routes.add(key)

        app = apps.get(route.app_key)
        if app is None:
            raise ConfigError(f"routes[{index}] references missing app {route.app_key}")
        if not app.enabled:
            raise ConfigError(f"routes[{index}] references disabled app {route.app_key}")
        profile = profiles.get(route.profile_id)
        if profile is None:
            raise ConfigError(f"routes[{index}] references missing profile {route.profile_id}")
        if not profile.enabled:
            raise ConfigError(f"routes[{index}] references disabled profile {route.profile_id}")
```

- [ ] **步驟 4：執行完整 loader 測試**

執行：

```bash
pytest -q tests/test_multi_profile_config_loader.py
```

預期：所有案例 PASS；`test_load_config_applies_defaults` 仍以省略 optional timeout 的 `VALID_YAML` 驗證預設值，參數化邊界測試只在自己的 local `base` 字串中加入 timeout 欄位。

- [ ] **步驟 5：提交任務 3**

```bash
git add multi_profile/config_loader.py tests/test_multi_profile_config_loader.py
git commit -m "feat(多租戶): 驗證設定關聯與執行邊界"
```

---

### 任務 4：建立 fail-safe ConfigRegistry

**文件：**
- 建立：`multi_profile/registry.py`
- 建立：`tests/test_multi_profile_registry.py`
- 修改：`multi_profile/__init__.py`

- [ ] **步驟 1：編寫 generation 與失敗保留測試**

建立 `tests/test_multi_profile_registry.py`：

```python
from pathlib import Path

import pytest

from multi_profile.config_loader import ConfigError
from multi_profile.registry import ConfigRegistry


CONFIG = """
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


@pytest.fixture
def environ():
    return {
        "FEISHU_OPS_APP_ID": "cli_test",
        "FEISHU_OPS_APP_SECRET": "secret_test",
    }


def test_registry_loads_generation_one(tmp_path, environ):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.format(working_dir=tmp_path), encoding="utf-8")
    registry = ConfigRegistry(path, environ=environ)

    snapshot = registry.load_initial()

    assert snapshot.generation == 1
    assert registry.snapshot() is snapshot


def test_reload_publishes_next_generation(tmp_path, environ):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.format(working_dir=tmp_path), encoding="utf-8")
    registry = ConfigRegistry(path, environ=environ)
    first = registry.load_initial()

    second = registry.reload()

    assert first.generation == 1
    assert second.generation == 2
    assert registry.snapshot() is second


def test_failed_reload_keeps_previous_snapshot(tmp_path, environ):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.format(working_dir=tmp_path), encoding="utf-8")
    registry = ConfigRegistry(path, environ=environ)
    first = registry.load_initial()
    path.write_text("version: [", encoding="utf-8")

    with pytest.raises(ConfigError):
        registry.reload()

    assert registry.snapshot() is first
    assert registry.snapshot().generation == 1


def test_snapshot_requires_successful_initial_load(tmp_path, environ):
    registry = ConfigRegistry(tmp_path / "missing.yaml", environ=environ)

    with pytest.raises(RuntimeError, match="not loaded"):
        registry.snapshot()
```

- [ ] **步驟 2：執行測試並確認 registry 不存在**

```bash
pytest -q tests/test_multi_profile_registry.py
```

預期：FAIL，包含 `ModuleNotFoundError: No module named 'multi_profile.registry'`。

- [ ] **步驟 3：實作 ConfigRegistry**

建立 `multi_profile/registry.py`：

```python
from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from pathlib import Path

from .config_loader import load_config
from .models import ConfigSnapshot


class ConfigRegistry:
    def __init__(
        self,
        path: str | Path,
        *,
        environ: Mapping[str, str] | None = None,
    ):
        self._path = Path(path)
        self._environ = environ if environ is not None else os.environ
        self._snapshot: ConfigSnapshot | None = None
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def load_initial(self) -> ConfigSnapshot:
        candidate = load_config(self._path, environ=self._environ, generation=1)
        with self._lock:
            if self._snapshot is not None:
                raise RuntimeError("config registry is already loaded")
            self._snapshot = candidate
            return candidate

    def reload(self) -> ConfigSnapshot:
        with self._lock:
            current = self._snapshot
            if current is None:
                raise RuntimeError("config registry is not loaded")
            next_generation = current.generation + 1

        candidate = load_config(
            self._path,
            environ=self._environ,
            generation=next_generation,
        )
        with self._lock:
            current = self._snapshot
            if current is None:
                raise RuntimeError("config registry is not loaded")
            if current.generation >= candidate.generation:
                candidate = load_config(
                    self._path,
                    environ=self._environ,
                    generation=current.generation + 1,
                )
            self._snapshot = candidate
            return candidate

    def snapshot(self) -> ConfigSnapshot:
        with self._lock:
            if self._snapshot is None:
                raise RuntimeError("config registry is not loaded")
            return self._snapshot
```

在 `multi_profile/__init__.py` 追加：

```python
from .registry import ConfigRegistry

__all__ += ["ConfigRegistry"]
```

- [ ] **步驟 4：執行 registry 與 loader 測試**

```bash
pytest -q tests/test_multi_profile_registry.py tests/test_multi_profile_config_loader.py
```

預期：全部 PASS。

- [ ] **步驟 5：提交任務 4**

```bash
git add multi_profile/registry.py multi_profile/__init__.py tests/test_multi_profile_registry.py
git commit -m "feat(多租戶): 加入設定快照 Registry"
```

---

### 任務 5：建立 TenantRouter 與隔離 ExecutionContext

**文件：**
- 建立：`multi_profile/router.py`
- 建立：`tests/test_multi_profile_router.py`
- 修改：`multi_profile/__init__.py`

- [ ] **步驟 1：編寫群、私聊與隔離鍵測試**

建立 `tests/test_multi_profile_router.py`：

```python
import pytest

from multi_profile.models import AppConfig, ProfileConfig, RouteConfig, create_snapshot
from multi_profile.router import RouteNotFound, TenantRouter


@pytest.fixture
def snapshot(tmp_path):
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
            working_dir=str(tmp_path),
        )
    }
    routes = (
        RouteConfig(
            app_key="ops-bot",
            chat_id="oc_prod_a",
            profile_id="prod-cn",
        ),
        RouteConfig(
            app_key="ops-bot",
            chat_id="oc_prod_b",
            profile_id="prod-cn",
        ),
    )
    return create_snapshot(3, apps, profiles, routes)


def test_group_route_builds_group_and_principal_keys(snapshot):
    context = TenantRouter(snapshot).resolve(
        platform="feishu",
        app_key="ops-bot",
        chat_type="group",
        chat_id="oc_prod_a",
        user_id="ou_user",
    )

    assert context.profile_id == "prod-cn"
    assert context.config_generation == 3
    assert context.group_scope_key == "feishu/ops-bot/group/oc_prod_a"
    assert context.principal_key == "feishu/ops-bot/group/oc_prod_a/user/ou_user"


def test_same_profile_different_groups_have_different_principals(snapshot):
    router = TenantRouter(snapshot)

    first = router.resolve(
        platform="feishu",
        app_key="ops-bot",
        chat_type="group",
        chat_id="oc_prod_a",
        user_id="ou_user",
    )
    second = router.resolve(
        platform="feishu",
        app_key="ops-bot",
        chat_type="group",
        chat_id="oc_prod_b",
        user_id="ou_user",
    )

    assert first.profile_id == second.profile_id
    assert first.principal_key != second.principal_key


def test_private_chat_uses_app_default_profile(snapshot):
    context = TenantRouter(snapshot).resolve(
        platform="feishu",
        app_key="ops-bot",
        chat_type="private",
        chat_id=None,
        user_id="ou_user",
    )

    assert context.profile_id == "prod-cn"
    assert context.group_scope_key is None
    assert context.principal_key == "feishu/ops-bot/private/ou_user"


def test_unmapped_group_fails_closed(snapshot):
    with pytest.raises(RouteNotFound, match="unmapped group"):
        TenantRouter(snapshot).resolve(
            platform="feishu",
            app_key="ops-bot",
            chat_type="group",
            chat_id="oc_unknown",
            user_id="ou_user",
        )


def test_unknown_app_private_chat_fails_closed(snapshot):
    with pytest.raises(RouteNotFound, match="unknown app"):
        TenantRouter(snapshot).resolve(
            platform="feishu",
            app_key="missing",
            chat_type="private",
            chat_id=None,
            user_id="ou_user",
        )
```

- [ ] **步驟 2：執行測試並確認 router 不存在**

```bash
pytest -q tests/test_multi_profile_router.py
```

預期：FAIL，包含 `ModuleNotFoundError: No module named 'multi_profile.router'`。

- [ ] **步驟 3：實作 TenantRouter**

建立 `multi_profile/router.py`：

```python
from __future__ import annotations

from .models import ConfigSnapshot, ExecutionContext, build_profile_fingerprint


class RouteNotFound(LookupError):
    pass


class TenantRouter:
    def __init__(self, snapshot: ConfigSnapshot):
        self._snapshot = snapshot
        self._group_routes = {
            (route.app_key, route.chat_id): route.profile_id
            for route in snapshot.routes
        }

    def resolve(
        self,
        *,
        platform: str,
        app_key: str,
        chat_type: str,
        chat_id: str | None,
        user_id: str,
    ) -> ExecutionContext:
        app = self._snapshot.apps.get(app_key)
        if app is None or not app.enabled:
            raise RouteNotFound(f"unknown app: {app_key}")

        if chat_type == "group":
            if not chat_id:
                raise RouteNotFound("group message is missing chat_id")
            profile_id = self._group_routes.get((app_key, chat_id))
            if profile_id is None:
                raise RouteNotFound(f"unmapped group: {app_key}/{chat_id}")
            group_scope_key = f"{platform}/{app_key}/group/{chat_id}"
            principal_key = f"{group_scope_key}/user/{user_id}"
        elif chat_type == "private":
            profile_id = app.default_profile
            group_scope_key = None
            principal_key = f"{platform}/{app_key}/private/{user_id}"
        else:
            raise RouteNotFound(f"unsupported chat_type: {chat_type}")

        profile = self._snapshot.profiles.get(profile_id)
        if profile is None or not profile.enabled:
            raise RouteNotFound(f"profile is unavailable: {profile_id}")

        return ExecutionContext(
            config_generation=self._snapshot.generation,
            platform=platform,
            app_key=app_key,
            chat_type=chat_type,
            chat_id=chat_id,
            user_id=user_id,
            principal_key=principal_key,
            group_scope_key=group_scope_key,
            profile_id=profile_id,
            profile=profile,
            profile_fingerprint=build_profile_fingerprint(profile),
        )
```

在 `multi_profile/__init__.py` 追加：

```python
from .router import RouteNotFound, TenantRouter

__all__ += ["RouteNotFound", "TenantRouter"]
```

- [ ] **步驟 4：執行 router、模型與 loader 測試**

```bash
pytest -q \
  tests/test_multi_profile_router.py \
  tests/test_multi_profile_models.py \
  tests/test_multi_profile_config_loader.py
```

預期：全部 PASS。

- [ ] **步驟 5：提交任務 5**

```bash
git add multi_profile/router.py multi_profile/__init__.py tests/test_multi_profile_router.py
git commit -m "feat(多租戶): 建立群與私聊路由核心"
```

---

### 任務 6：加入 feature flag、範例設定與 runtime 忽略規則

**文件：**
- 建立：`multi_profile/feature_flags.py`
- 建立：`tests/test_multi_profile_feature_flags.py`
- 建立：`multi_profile_config.example.yaml`
- 修改：`multi_profile/__init__.py`
- 修改：`.env.example`
- 修改：`.gitignore`

- [ ] **步驟 1：編寫 feature flag 失敗測試**

建立 `tests/test_multi_profile_feature_flags.py`：

```python
from pathlib import Path

from multi_profile.feature_flags import config_path, is_enabled


def test_feature_flag_defaults_to_false():
    assert is_enabled({}) is False


def test_feature_flag_accepts_explicit_true_values():
    for value in ("true", "1", "yes", "TRUE"):
        assert is_enabled({"MULTI_PROFILE_ENABLED": value}) is True


def test_feature_flag_rejects_other_values():
    for value in ("false", "0", "no", "unexpected", ""):
        assert is_enabled({"MULTI_PROFILE_ENABLED": value}) is False


def test_config_path_defaults_to_project_file(tmp_path):
    assert config_path({}, project_dir=tmp_path) == tmp_path / "multi_profile_config.yaml"


def test_config_path_uses_explicit_absolute_path(tmp_path):
    expected = tmp_path / "custom.yaml"
    assert config_path(
        {"MULTI_PROFILE_CONFIG": str(expected)},
        project_dir=Path("/unused"),
    ) == expected
```

- [ ] **步驟 2：執行測試並確認 module 不存在**

```bash
pytest -q tests/test_multi_profile_feature_flags.py
```

預期：FAIL，包含 `ModuleNotFoundError: No module named 'multi_profile.feature_flags'`。

- [ ] **步驟 3：實作 feature flag API**

建立 `multi_profile/feature_flags.py`：

```python
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


_TRUE_VALUES = {"true", "1", "yes"}


def is_enabled(environ: Mapping[str, str] | None = None) -> bool:
    values = environ if environ is not None else os.environ
    return values.get("MULTI_PROFILE_ENABLED", "false").strip().lower() in _TRUE_VALUES


def config_path(
    environ: Mapping[str, str] | None = None,
    *,
    project_dir: str | Path,
) -> Path:
    values = environ if environ is not None else os.environ
    configured = values.get("MULTI_PROFILE_CONFIG", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(project_dir) / "multi_profile_config.yaml"
```

在 `multi_profile/__init__.py` 追加：

```python
from .feature_flags import config_path, is_enabled

__all__ += ["config_path", "is_enabled"]
```

- [ ] **步驟 4：執行 feature flag 測試**

```bash
pytest -q tests/test_multi_profile_feature_flags.py
```

預期：5 passed。

- [ ] **步驟 5：建立不含 Secret 的完整範例 YAML**

建立 `multi_profile_config.example.yaml`：

```yaml
version: 1

apps:
  ops-bot:
    enabled: true
    app_id_env: FEISHU_OPS_APP_ID
    app_secret_env: FEISHU_OPS_APP_SECRET
    default_profile: prod-cn

profiles:
  prod-cn:
    enabled: true
    aws_profile: production
    aws_region: cn-northwest-1
    expected_account_id: "123456789012"
    kiro_agent: my-dev-bot
    model: claude-sonnet
    alert_agent: ec2-alert-analyzer
    alert_model: claude-sonnet
    working_dir: /home/ubuntu/kiro-devops
    sync_timeout: 120
    async_timeout: 1800
    alert_timeout: 300

routes:
  - app: ops-bot
    chat_id: oc_xxxxxxxxxx
    profile: prod-cn
    poll_alerts: true
```

- [ ] **步驟 6：在 `.env.example` 加入預設關閉設定**

在 Kiro Agent／模型設定之後加入：

```dotenv
# === 多飛書 App / 多 AWS Profile（分階段功能，預設關閉）===
# 正式切換前必須完成設定、STS 與回滾驗證
MULTI_PROFILE_ENABLED=false
# MULTI_PROFILE_CONFIG=/home/ubuntu/kiro-devops/multi_profile_config.yaml

# 每個飛書 App 的 Secret 仍只放 .env；YAML 只引用變數名稱
# FEISHU_OPS_APP_ID=cli_xxxxxxxxxxxxxxxx
# FEISHU_OPS_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

- [ ] **步驟 7：更新 `.gitignore`**

在檔案尾端加入：

```gitignore
# Multi-profile runtime state and local active config
runtime/
multi_profile_config.yaml
```

不要忽略 `multi_profile_config.example.yaml`。

- [ ] **步驟 8：執行 feature flag 與設定測試**

```bash
pytest -q \
  tests/test_multi_profile_feature_flags.py \
  tests/test_multi_profile_config_loader.py
```

預期：全部 PASS。

- [ ] **步驟 9：確認本計畫沒有接線到 gateway**

執行：

```bash
git diff -- gateway.py message_handler.py kiro_executor.py session_router.py \
  alert_analysis.py platform_dispatcher.py adapters dashboard
```

預期：沒有輸出。

- [ ] **步驟 10：提交任務 6**

```bash
git add \
  multi_profile/feature_flags.py \
  multi_profile/__init__.py \
  tests/test_multi_profile_feature_flags.py \
  multi_profile_config.example.yaml \
  .env.example \
  .gitignore
git commit -m "feat(多租戶): 加入停用預設與設定範例"
```

---

### 任務 7：計畫級回歸驗證與公開介面確認

**文件：**
- 不新增檔案；只驗證任務 1–6 的結果。

- [ ] **步驟 1：執行多 profile 核心 targeted tests**

```bash
pytest -q \
  tests/test_multi_profile_models.py \
  tests/test_multi_profile_config_loader.py \
  tests/test_multi_profile_registry.py \
  tests/test_multi_profile_router.py \
  tests/test_multi_profile_feature_flags.py
```

預期：全部 PASS，0 failed。

- [ ] **步驟 2：執行直接相關 legacy 回歸**

```bash
pytest -q \
  tests/test_config_store.py \
  tests/test_platform_dispatcher.py \
  tests/test_group_alert_detection.py
```

預期：全部 PASS，既有 Dashboard 設定、單一 Dispatcher 與群告警行為不變。

- [ ] **步驟 3：執行完整測試套件**

```bash
pytest -q
```

預期：0 failed。若存在與本計畫無關的既有失敗，停止並依 systematic-debugging 流程確認基線，不得忽略。

- [ ] **步驟 4：執行 Python 編譯檢查**

```bash
python3 -m compileall -q multi_profile tests
```

預期：exit 0，沒有語法錯誤。

- [ ] **步驟 5：確認 feature flag 預設關閉且 gateway 無差異**

```bash
python3 - <<'PY'
from multi_profile.feature_flags import is_enabled
assert is_enabled({}) is False
print("legacy mode remains the default")
PY

git diff HEAD~6..HEAD -- \
  gateway.py message_handler.py kiro_executor.py session_router.py \
  alert_analysis.py platform_dispatcher.py adapters dashboard
```

預期：第一段輸出 `legacy mode remains the default`；第二段沒有輸出。

- [ ] **步驟 6：檢查公開介面可由計畫 2 匯入**

```bash
python3 - <<'PY'
from multi_profile import (
    ConfigError,
    ConfigRegistry,
    ConfigSnapshot,
    ExecutionContext,
    ProfileConfig,
    RouteNotFound,
    TenantRouter,
    config_path,
    is_enabled,
    load_config,
)
print("multi_profile public API import OK")
PY
```

預期：輸出 `multi_profile public API import OK`。

- [ ] **步驟 7：確認工作區與提交範圍**

```bash
git status --short
git log --oneline -6
```

預期：沒有未提交的本計畫檔案；最近 6 筆提交分別對應任務 1–6。

---

## 完成標準

- 嚴格 YAML loader 實作規格第 6 節的必填欄位、預設值與未知欄位拒絕。
- ConfigSnapshot 及 ExecutionContext 不可變；generation reload 失敗時保留舊 snapshot。
- TenantRouter 對群使用 `(app_key, chat_id)`，對私聊使用 App default profile。
- 未映射群在核心層回傳 `RouteNotFound`，不提供 fallback。
- 多群映射同一 profile 時仍產生不同 principal key。
- `MULTI_PROFILE_ENABLED` 預設 false，且本計畫沒有修改或接線 gateway/runtime。
- Targeted tests、legacy tests、完整 pytest 與 compileall 全部通過。

## 不在本計畫範圍

- 不建立 Kiro 子程序環境或 SessionStore。
- 不修改 Session／記憶 key。
- 不建立多個 FeishuAdapter 或 AppManager。
- 不修改 PlatformDispatcher 的 registry key。
- 不路由群告警 ExecutionContext。
- 不執行 STS、Agent 或模型外部驗證；這屬於計畫 4。
- 不建立 Dashboard API／UI、revision、原子寫檔或 last-known-good。
- 不啟用 `MULTI_PROFILE_ENABLED=true`。

## 計畫 2 可依賴的公開介面

```python
from multi_profile import (
    ConfigError,
    ConfigRegistry,
    ConfigSnapshot,
    ExecutionContext,
    ProfileConfig,
    RouteNotFound,
    TenantRouter,
    config_path,
    is_enabled,
    load_config,
)
```

計畫 2 必須直接接收 `ExecutionContext`／`ProfileConfig`，不得重新解析 YAML 或讀取全域 profile。
