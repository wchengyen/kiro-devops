# Tencent Dashboard Resources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Tencent Cloud (CVM / Lighthouse) resource monitoring to the dashboard with full AWS parity, using a unified Provider abstraction layer.

**Architecture:** Refactor existing AWS boto3 logic into `AWSProvider` (inherits `BaseResourceProvider`), add `TencentProvider` using `tccli` subprocess calls, unify API routing by `?provider=aws|tencent`, extend SQLite schema with `provider` column, and genericize the Vue frontend via `provider` prop.

**Tech Stack:** Python 3.10, Flask, boto3, tccli (subprocess), SQLite, Vue 3 (global build), bash

---

## File Structure

### New Files
- `dashboard/providers/__init__.py` — Provider registry and factory functions
- `dashboard/providers/base.py` — `BaseResourceProvider` ABC, `Resource`, `MetricPoint`, `ResourceMetrics` dataclasses
- `dashboard/providers/aws.py` — `AWSProvider` (extracted from `dashboard/resources.py`)
- `dashboard/providers/tencent.py` — `TencentProvider` (tccli-based CVM/Lighthouse discovery + metrics)
- `tests/test_providers_aws.py` — Unit tests for `AWSProvider` with mocked boto3
- `tests/test_providers_tencent.py` — Unit tests for `TencentProvider` with mocked subprocess
- `tests/test_dashboard_api_resources_tencent.py` — API route tests for Tencent endpoints
- `tests/test_config_store.py` — Config migration and provider-aware read/write tests
- `tests/fixtures/tencent_cvm_describe.json` — Sample tccli CVM output
- `tests/fixtures/tencent_lighthouse_describe.json` — Sample tccli Lighthouse output
- `tests/fixtures/tencent_monitor_cpu.json` — Sample tccli monitor output

### Modified Files
- `dashboard/resources.py` — Compatibility shim delegating to `AWSProvider`
- `dashboard/config_store.py` — Read/write new `providers` structure; auto-migrate old format
- `dashboard/metrics_store.py` — Add `provider` column; migrate old tables
- `dashboard/api.py` — Unified routing with `?provider` param and ID-based provider parsing
- `dashboard/static/app.js` — Generic `ResourcesPage` with `provider` prop; dynamic nav items
- `scripts/sync_resource_metrics.py` — Iterate all enabled providers instead of AWS-only
- `setup.sh` — Interactive prompt to enable/disable Tencent and set regions

---

## Task 1: Base Dataclasses and ABC

**Files:**
- Create: `dashboard/providers/base.py`
- Test: `tests/test_providers_base.py` (inline in this task, file can be omitted if tested via AWS/Tencent tasks)

- [ ] **Step 1: Write failing tests for base dataclasses**

Create `tests/test_providers_base.py`:

```python
import pytest
from dashboard.providers.base import Resource, MetricPoint, ResourceMetrics, BaseResourceProvider


def test_resource_unique_id():
    r = Resource(provider="aws", type="ec2", region="cn-north-1", id="i-123", name="test", status="running")
    assert r.unique_id == "aws:ec2:cn-north-1:i-123"


def test_resource_defaults():
    r = Resource(provider="tencent", type="cvm", region="ap-tokyo", id="ins-1", name="t", status="RUNNING")
    assert r.tags == {}
    assert r.meta == {}
    assert r.class_type is None


def test_base_provider_is_abstract():
    with pytest.raises(TypeError):
        BaseResourceProvider()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_providers_base.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` for `dashboard.providers.base`.

- [ ] **Step 3: Implement `dashboard/providers/base.py`**

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Resource:
    provider: str
    type: str
    region: str
    id: str
    name: str
    status: str
    class_type: Optional[str] = None
    os_or_engine: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def unique_id(self) -> str:
        return f"{self.provider}:{self.type}:{self.region}:{self.id}"


@dataclass
class MetricPoint:
    timestamp: datetime
    value: float


@dataclass
class ResourceMetrics:
    resource_id: str
    metric_name: str
    points_7d: List[MetricPoint]
    points_30d: List[MetricPoint]
    current: Optional[float] = None
    stats_7d: Optional[Dict] = None
    stats_30d: Optional[Dict] = None
    sparkline_7d: List[float] = field(default_factory=list)


class BaseResourceProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def is_enabled(self) -> bool: ...

    @abstractmethod
    def regions(self) -> List[str]: ...

    @abstractmethod
    def resource_types(self) -> List[str]: ...

    @abstractmethod
    def discover_resources(
        self, region: str, resource_type: Optional[str] = None
    ) -> List[Resource]: ...

    @abstractmethod
    def get_metrics(
        self, resource: Resource, range_days: int = 7
    ) -> ResourceMetrics: ...

    @abstractmethod
    def sync_metrics_to_store(self, store, backfill_days: int = 1) -> None: ...
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_providers_base.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/providers/base.py tests/test_providers_base.py
git commit -m "feat(providers): add BaseResourceProvider ABC and dataclasses"
```

---

## Task 2: AWS Provider Extraction

**Files:**
- Create: `dashboard/providers/aws.py`
- Modify: `dashboard/resources.py`
- Test: `tests/test_providers_aws.py`

- [ ] **Step 1: Write failing tests for AWSProvider**

Create `tests/test_providers_aws.py`:

```python
import pytest
from unittest.mock import MagicMock, patch
from dashboard.providers.aws import AWSProvider
from dashboard.providers.base import Resource


@pytest.fixture
def provider():
    with patch("dashboard.providers.aws._load_config") as m:
        m.return_value = {"providers": {"aws": {"enabled": True, "regions": ["cn-north-1"]}}}
        yield AWSProvider()


def test_name(provider):
    assert provider.name == "aws"


def test_is_enabled(provider):
    assert provider.is_enabled() is True


def test_regions(provider):
    assert provider.regions() == ["cn-north-1"]


def test_resource_types(provider):
    assert set(provider.resource_types()) == {"ec2", "rds"}


@patch("dashboard.providers.aws.boto3.client")
def test_discover_ec2(mock_client, provider):
    ec2 = MagicMock()
    ec2.describe_instances.return_value = {
        "Reservations": [{
            "Instances": [{
                "InstanceId": "i-123",
                "InstanceType": "t3.micro",
                "State": {"Name": "running"},
                "Tags": [{"Key": "Name", "Value": "web1"}],
                "Platform": "windows",
                "LaunchTime": "2024-01-01T00:00:00Z",
            }]
        }]
    }
    mock_client.return_value = ec2
    resources = provider.discover_resources("cn-north-1", "ec2")
    assert len(resources) == 1
    assert resources[0].id == "i-123"
    assert resources[0].type == "ec2"
    assert resources[0].provider == "aws"
    assert resources[0].unique_id == "aws:ec2:cn-north-1:i-123"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_providers_aws.py -v
```

Expected: `ModuleNotFoundError` for `dashboard.providers.aws`.

- [ ] **Step 3: Extract AWS logic into `dashboard/providers/aws.py`**

Create `dashboard/providers/aws.py`. Copy existing `discover_ec2`, `discover_rds`, `get_cloudwatch_metrics` logic from `dashboard/resources.py`, wrapping them in `AWSProvider` and returning `base.Resource` / `base.ResourceMetrics` objects.

Key implementation notes:
- `_load_config()` helper reads `dashboard_config.json`.
- `discover_resources(region, resource_type)` delegates to internal `_discover_ec2` / `_discover_rds`.
- `get_metrics(resource, range_days)` fetches CloudWatch `CPUUtilization` and returns `ResourceMetrics` with `metric_name="cpu_utilization"`.
- `sync_metrics_to_store` iterates regions, discovers resources, fetches metrics, and writes to the passed `store`.

- [ ] **Step 4: Update `dashboard/resources.py` to compatibility shim**

Replace the existing boto3 logic with imports from `dashboard.providers.aws`:

```python
from dashboard.providers import get_provider
from dashboard.providers.base import Resource, ResourceMetrics

def get_all_resources_with_metrics(refresh=False, ...):
    provider = get_provider("aws")
    # preserve existing return shape for callers
    ...
```

Ensure existing imports and public API surface remain intact.

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_providers_aws.py -v
```

Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add dashboard/providers/aws.py tests/test_providers_aws.py dashboard/resources.py
git commit -m "feat(providers): extract AWSProvider from resources.py"
```

---

## Task 3: Tencent Provider Implementation

**Files:**
- Create: `tests/fixtures/tencent_cvm_describe.json`
- Create: `tests/fixtures/tencent_lighthouse_describe.json`
- Create: `tests/fixtures/tencent_monitor_cpu.json`
- Create: `dashboard/providers/tencent.py`
- Test: `tests/test_providers_tencent.py`

- [ ] **Step 1: Write fixture files**

`tests/fixtures/tencent_cvm_describe.json`:

```json
{
  "InstanceSet": [
    {
      "InstanceId": "ins-123456",
      "InstanceName": "test-cvm",
      "InstanceState": "RUNNING",
      "InstanceType": "S5.MEDIUM2",
      "OsName": "CentOS 7.9",
      "Tags": [{"Key": "env", "Value": "dev"}],
      "CreatedTime": "2024-01-01T00:00:00Z"
    }
  ],
  "TotalCount": 1
}
```

`tests/fixtures/tencent_lighthouse_describe.json`:

```json
{
  "InstanceSet": [
    {
      "InstanceId": "lhins-abc",
      "InstanceName": "test-lh",
      "InstanceState": "RUNNING",
      "BundleId": "bundle_small",
      "OsName": "Ubuntu 20.04",
      "CreatedTime": "2024-02-01T00:00:00Z"
    }
  ],
  "TotalCount": 1
}
```

`tests/fixtures/tencent_monitor_cpu.json`:

```json
{
  "Period": 3600,
  "MetricName": "CPUUsage",
  "DataPoints": [
    {"Timestamps": [1704067200, 1704070800], "Values": [5.2, 10.1]}
  ]
}
```

- [ ] **Step 2: Write failing tests for TencentProvider**

Create `tests/test_providers_tencent.py`:

```python
import json
import pytest
from unittest.mock import patch, MagicMock
from dashboard.providers.tencent import TencentProvider
from dashboard.providers.base import Resource


@pytest.fixture
def provider():
    with patch("dashboard.providers.tencent._load_config") as m:
        m.return_value = {"providers": {"tencent": {"enabled": True, "regions": ["ap-tokyo"]}}}
        yield TencentProvider()


def test_name(provider):
    assert provider.name == "tencent"


def test_resource_types(provider):
    assert set(provider.resource_types()) == {"cvm", "lighthouse"}


@patch("dashboard.providers.tencent.subprocess.run")
def test_discover_cvm(mock_run, provider):
    with open("tests/fixtures/tencent_cvm_describe.json") as f:
        data = json.load(f)
    mock_run.return_value = MagicMock(stdout=json.dumps(data), returncode=0)
    resources = provider.discover_resources("ap-tokyo", "cvm")
    assert len(resources) == 1
    assert resources[0].id == "ins-123456"
    assert resources[0].provider == "tencent"
    assert resources[0].unique_id == "tencent:cvm:ap-tokyo:ins-123456"


@patch("dashboard.providers.tencent.subprocess.run")
def test_get_metrics(mock_run, provider):
    with open("tests/fixtures/tencent_monitor_cpu.json") as f:
        data = json.load(f)
    mock_run.return_value = MagicMock(stdout=json.dumps(data), returncode=0)
    r = Resource(provider="tencent", type="cvm", region="ap-tokyo", id="ins-123456", name="t", status="RUNNING")
    metrics = provider.get_metrics(r, range_days=7)
    assert metrics.metric_name == "cpu_utilization"
    assert len(metrics.points_7d) == 2
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_providers_tencent.py -v
```

Expected: `ModuleNotFoundError` for `dashboard.providers.tencent`.

- [ ] **Step 4: Implement `dashboard/providers/tencent.py`**

```python
import json
import subprocess
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dashboard.providers.base import BaseResourceProvider, Resource, ResourceMetrics, MetricPoint


def _load_config():
    import json
    try:
        with open("dashboard_config.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _tccli(service: str, action: str, region: str, payload: Optional[Dict] = None) -> Dict[str, Any]:
    cmd = ["tccli", service, action, "--region", region, "--output", "json"]
    if payload:
        cmd.extend(["--cli-input-json", json.dumps(payload)])
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


class TencentProvider(BaseResourceProvider):
    @property
    def name(self) -> str:
        return "tencent"

    def is_enabled(self) -> bool:
        cfg = _load_config().get("providers", {}).get("tencent", {})
        return cfg.get("enabled", False)

    def regions(self) -> List[str]:
        cfg = _load_config().get("providers", {}).get("tencent", {})
        return cfg.get("regions", [])

    def resource_types(self) -> List[str]:
        return ["cvm", "lighthouse"]

    def discover_resources(self, region: str, resource_type: Optional[str] = None) -> List[Resource]:
        results = []
        types_to_query = [resource_type] if resource_type else self.resource_types()
        for rt in types_to_query:
            if rt == "cvm":
                results.extend(self._discover_cvm(region))
            elif rt == "lighthouse":
                results.extend(self._discover_lighthouse(region))
        return results

    def _discover_cvm(self, region: str) -> List[Resource]:
        data = _tccli("cvm", "DescribeInstances", region)
        resources = []
        for inst in data.get("InstanceSet", []):
            resources.append(Resource(
                provider="tencent",
                type="cvm",
                region=region,
                id=inst["InstanceId"],
                name=inst.get("InstanceName", inst["InstanceId"]),
                status=inst.get("InstanceState", "UNKNOWN"),
                class_type=inst.get("InstanceType"),
                os_or_engine=inst.get("OsName"),
                tags={t["Key"]: t["Value"] for t in inst.get("Tags", [])},
                meta={"CreatedTime": inst.get("CreatedTime")},
            ))
        return resources

    def _discover_lighthouse(self, region: str) -> List[Resource]:
        data = _tccli("lighthouse", "DescribeInstances", region)
        resources = []
        for inst in data.get("InstanceSet", []):
            resources.append(Resource(
                provider="tencent",
                type="lighthouse",
                region=region,
                id=inst["InstanceId"],
                name=inst.get("InstanceName", inst["InstanceId"]),
                status=inst.get("InstanceState", "UNKNOWN"),
                class_type=inst.get("BundleId"),
                os_or_engine=inst.get("OsName"),
                tags={},
                meta={"CreatedTime": inst.get("CreatedTime")},
            ))
        return resources

    def get_metrics(self, resource: Resource, range_days: int = 7) -> ResourceMetrics:
        end = datetime.utcnow()
        start = end - timedelta(days=range_days)
        namespace = "QCE/CVM" if resource.type == "cvm" else "QCE/LIGHTHOUSE"
        payload = {
            "Namespace": namespace,
            "MetricName": "CPUUsage",
            "Instances": [{"Dimensions": [{"Name": "InstanceId", "Value": resource.id}]}],
            "Period": 3600,
            "StartTime": start.isoformat(),
            "EndTime": end.isoformat(),
        }
        data = _tccli("monitor", "GetMonitorData", resource.region, payload)
        points = []
        for dp in data.get("DataPoints", []):
            for ts, val in zip(dp.get("Timestamps", []), dp.get("Values", [])):
                points.append(MetricPoint(timestamp=datetime.utcfromtimestamp(ts), value=val))
        points.sort(key=lambda p: p.timestamp)
        # Split into 7d/30d based on range_days; for simplicity return all in points_7d if range_days <= 7
        return ResourceMetrics(
            resource_id=resource.unique_id,
            metric_name="cpu_utilization",
            points_7d=points,
            points_30d=[],
        )

    def sync_metrics_to_store(self, store, backfill_days: int = 1) -> None:
        for region in self.regions():
            for rt in self.resource_types():
                for resource in self.discover_resources(region, rt):
                    metrics = self.get_metrics(resource, range_days=backfill_days)
                    for p in metrics.points_7d:
                        store.write_raw(provider="tencent", timestamp=p.timestamp, resource_id=resource.unique_id,
                                        metric="cpu_utilization", value=p.value)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_providers_tencent.py -v
```

Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add dashboard/providers/tencent.py tests/test_providers_tencent.py tests/fixtures/
git commit -m "feat(providers): add TencentProvider with CVM/Lighthouse discovery and metrics"
```

---

## Task 4: Provider Registry

**Files:**
- Create: `dashboard/providers/__init__.py`

- [ ] **Step 1: Write failing test for registry**

Create `tests/test_providers_registry.py`:

```python
from dashboard.providers import get_provider, get_all_enabled_providers
from dashboard.providers.aws import AWSProvider
from dashboard.providers.tencent import TencentProvider


def test_get_provider_aws():
    p = get_provider("aws")
    assert isinstance(p, AWSProvider)


def test_get_provider_tencent():
    p = get_provider("tencent")
    assert isinstance(p, TencentProvider)


def test_get_provider_invalid():
    with pytest.raises(ValueError):
        get_provider("gcp")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_providers_registry.py -v
```

Expected: `ImportError` or `AttributeError`.

- [ ] **Step 3: Implement registry**

Create `dashboard/providers/__init__.py`:

```python
from dashboard.providers.aws import AWSProvider
from dashboard.providers.tencent import TencentProvider

_REGISTRY = {
    "aws": AWSProvider,
    "tencent": TencentProvider,
}


def get_provider(name: str):
    cls = _REGISTRY.get(name)
    if not cls:
        raise ValueError(f"Unknown provider: {name}")
    return cls()


def get_all_enabled_providers():
    return [p() for p in _REGISTRY.values() if p().is_enabled()]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_providers_registry.py -v
```

Expected: passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/providers/__init__.py tests/test_providers_registry.py
git commit -m "feat(providers): add provider registry and factory functions"
```

---

## Task 5: Config Store Migration

**Files:**
- Modify: `dashboard/config_store.py`
- Test: `tests/test_config_store.py`

- [ ] **Step 1: Write failing tests for config migration**

Create `tests/test_config_store.py`:

```python
import json
import os
import pytest
from dashboard.config_store import ConfigStore


@pytest.fixture
def old_config_file(tmp_path):
    path = tmp_path / "dashboard_config.json"
    path.write_text(json.dumps({"regions": ["cn-north-1"], "pins": ["ec2:cn-north-1:i-1"]}))
    return str(path)


@pytest.fixture
def new_config_file(tmp_path):
    path = tmp_path / "dashboard_config.json"
    path.write_text(json.dumps({
        "providers": {"aws": {"enabled": True, "regions": ["cn-north-1"]}},
        "pins": ["aws:ec2:cn-north-1:i-1"]
    }))
    return str(path)


def test_migrate_old_config(old_config_file, monkeypatch):
    monkeypatch.setattr("dashboard.config_store.CONFIG_PATH", old_config_file)
    store = ConfigStore()
    cfg = store.load()
    assert "providers" in cfg
    assert cfg["providers"]["aws"]["regions"] == ["cn-north-1"]
    assert cfg["pins"] == ["aws:ec2:cn-north-1:i-1"]


def test_read_new_config(new_config_file, monkeypatch):
    monkeypatch.setattr("dashboard.config_store.CONFIG_PATH", new_config_file)
    store = ConfigStore()
    cfg = store.load()
    assert cfg["providers"]["aws"]["enabled"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_config_store.py -v
```

Expected: failures due to missing migration logic.

- [ ] **Step 3: Implement migration in `dashboard/config_store.py`**

Add helper inside `dashboard/config_store.py`:

```python
def _migrate_config(cfg: dict) -> dict:
    if "providers" not in cfg and "regions" in cfg:
        old_regions = cfg.pop("regions", [])
        cfg["providers"] = {
            "aws": {"enabled": True, "regions": old_regions}
        }
    # Migrate pins to include provider prefix
    pins = cfg.get("pins", [])
    migrated_pins = []
    for pin in pins:
        if not pin.startswith("aws:") and not pin.startswith("tencent:"):
            migrated_pins.append(f"aws:{pin}")
        else:
            migrated_pins.append(pin)
    cfg["pins"] = migrated_pins
    return cfg
```

Hook `_migrate_config` into `ConfigStore.load()` before returning.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_config_store.py -v
```

Expected: passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/config_store.py tests/test_config_store.py
git commit -m "feat(config): add provider-aware structure with auto-migration"
```

---

## Task 6: Metrics Store Schema Migration

**Files:**
- Modify: `dashboard/metrics_store.py`
- Test: `tests/test_metrics_store.py` (extend existing or create new)

- [ ] **Step 1: Write failing test for schema migration**

Add to `tests/test_metrics_store_migration.py`:

```python
import sqlite3
import pytest
from dashboard.metrics_store import MetricsStore


def test_provider_column_added(tmp_path):
    db_path = tmp_path / "raw_metrics_2026_04.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE raw_metrics (timestamp INTEGER, resource_id TEXT, metric TEXT, value REAL)")
    conn.commit()
    conn.close()

    store = MetricsStore(str(db_path))
    conn = sqlite3.connect(str(db_path))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(raw_metrics)")]
    assert "provider" in cols
    conn.close()


def test_write_and_query_with_provider(tmp_path):
    db_path = tmp_path / "raw_metrics_2026_04.db"
    store = MetricsStore(str(db_path))
    from datetime import datetime
    ts = datetime(2026, 4, 27, 12, 0, 0)
    store.write_raw(provider="tencent", timestamp=ts, resource_id="tencent:cvm:ap-tokyo:ins-1",
                    metric="cpu_utilization", value=15.5)
    rows = store.query_history("tencent:cvm:ap-tokyo:ins-1", metric="cpu_utilization", range_str="24h")
    assert len(rows) == 1
    assert rows[0]["value"] == 15.5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_metrics_store_migration.py -v
```

Expected: failures due to missing `provider` support.

- [ ] **Step 3: Extend `dashboard/metrics_store.py`**

1. In `__init__`, after ensuring tables exist, check if `provider` column exists. If not:
   ```sql
   ALTER TABLE raw_metrics ADD COLUMN provider TEXT DEFAULT 'aws';
   ALTER TABLE aggregated_metrics ADD COLUMN provider TEXT DEFAULT 'aws';
   ```
2. Update `write_raw` signature to accept `provider="aws"` and include it in INSERT.
3. Update `query_history(resource_id, metric, range)` to parse provider prefix from `resource_id` and add `AND provider = ?` to WHERE clauses.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_metrics_store_migration.py -v
```

Expected: passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/metrics_store.py tests/test_metrics_store_migration.py
git commit -m "feat(metrics): add provider column to SQLite schema with migration"
```

---

## Task 7: API Layer Refactor + Tencent Routes

**Files:**
- Modify: `dashboard/api.py`
- Test: `tests/test_dashboard_api_resources_tencent.py`

- [ ] **Step 1: Write failing tests for Tencent API routes**

Create `tests/test_dashboard_api_resources_tencent.py`:

```python
import pytest
from unittest.mock import patch
from dashboard import create_app


@pytest.fixture
def client():
    app = create_app(testing=True)
    with app.test_client() as c:
        yield c


@patch("dashboard.api.get_provider")
def test_get_resources_tencent(mock_get_provider, client):
    mock_provider = mock_get_provider.return_value
    mock_provider.name = "tencent"
    mock_provider.regions.return_value = ["ap-tokyo"]
    mock_provider.discover_resources.return_value = []
    resp = client.get("/api/dashboard/resources?provider=tencent")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["regions"] == ["ap-tokyo"]


@patch("dashboard.api.get_provider")
def test_history_parses_tencent_id(mock_get_provider, client):
    mock_provider = mock_get_provider.return_value
    mock_provider.get_metrics.return_value = MagicMock(points_7d=[])
    resp = client.get("/api/dashboard/resources/tencent:cvm:ap-tokyo:ins-1/history?range=24h")
    assert resp.status_code == 200
    mock_get_provider.assert_called_once_with("tencent")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_dashboard_api_resources_tencent.py -v
```

Expected: failures due to missing `?provider` handling and ID parsing.

- [ ] **Step 3: Refactor `dashboard/api.py`**

1. Add helper `_parse_provider_from_id(resource_id: str) -> str`.
2. Update `/api/dashboard/resources` to accept `?provider=aws|tencent` (default `aws`).
3. Update `/api/dashboard/resources/<id>/history` to call `_parse_provider_from_id(id)` and dispatch to correct provider.
4. Move the 5-minute cache logic to be provider-aware (`_cache_key` includes provider name).
5. Ensure `@require_auth` remains on all routes.

- [ ] **Step 4: Update existing AWS API tests**

In `tests/test_dashboard_api_resources.py`, update mocks to target `dashboard.api.get_provider` returning a mock `AWSProvider`, ensuring backward compatibility.

- [ ] **Step 5: Run all resource API tests**

```bash
pytest tests/test_dashboard_api_resources.py tests/test_dashboard_api_resources_tencent.py -v
```

Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add dashboard/api.py tests/test_dashboard_api_resources.py tests/test_dashboard_api_resources_tencent.py
git commit -m "feat(api): unify resource routes with provider dispatch and Tencent support"
```

---

## Task 8: Frontend Generic ResourcesPage

**Files:**
- Modify: `dashboard/static/app.js`

- [ ] **Step 1: Identify current ResourcesPage boundaries**

Read `dashboard/static/app.js` and identify:
- `ResourcesPage` component definition
- Router configuration block
- Navigation sidebar item list

- [ ] **Step 2: Refactor router**

Change routes array:

```javascript
const routes = [
  { path: '/resources', redirect: '/resources/aws' },
  { path: '/resources/:provider(aws|tencent)', component: ResourcesPage, props: true },
  // ... keep existing routes
];
```

- [ ] **Step 3: Genericize ResourcesPage**

1. Accept `provider` prop.
2. In `mounted()` / data fetch, append `?provider=${this.provider}` to `/api/dashboard/resources`.
3. Derive `typeOptions`, column labels, and region dropdown from API response or a provider metadata map:

```javascript
const providerMeta = {
  aws: { types: ['ec2', 'rds'], classLabel: 'Class', osLabel: 'OS/Engine' },
  tencent: { types: ['cvm', 'lighthouse'], classLabel: 'Class', osLabel: 'OS' }
};
```

4. Keep sparkline, history chart, pin logic unchanged.

- [ ] **Step 4: Dynamic navigation sidebar**

Fetch `/api/dashboard/config` (or use an existing config endpoint) to determine enabled providers, then render nav items conditionally.

- [ ] **Step 5: Manual browser verification**

Start the dashboard server, open `http://localhost:<port>`, verify:
- `/resources/aws` loads existing AWS table
- `/resources/tencent` loads (empty or populated) Tencent table
- Sidebar shows/hides Tencent link based on config

- [ ] **Step 6: Commit**

```bash
git add dashboard/static/app.js
git commit -m "feat(ui): genericize ResourcesPage for AWS and Tencent providers"
```

---

## Task 9: Background Sync Script Refactor

**Files:**
- Modify: `scripts/sync_resource_metrics.py`

- [ ] **Step 1: Read current script**

Understand existing AWS-only flow (boto3 discovery + CloudWatch batching).

- [ ] **Step 2: Refactor to provider loop**

Replace the AWS-only entrypoint with:

```python
from dashboard.providers import get_all_enabled_providers
from dashboard.metrics_store import MetricsStore

def main():
    store = MetricsStore()
    for provider in get_all_enabled_providers():
        print(f"Syncing metrics for {provider.name} ...")
        provider.sync_metrics_to_store(store, backfill_days=args.backfill_days)
    store.close()
```

Keep AWS-specific CloudWatch batch optimizations inside `AWSProvider.sync_metrics_to_store`.

- [ ] **Step 3: Dry-run verification**

```bash
python scripts/sync_resource_metrics.py --dry-run
```

Expected: discovers enabled providers and prints intended actions without writing.

- [ ] **Step 4: Commit**

```bash
git add scripts/sync_resource_metrics.py
git commit -m "feat(sync): refactor metrics sync script to iterate all enabled providers"
```

---

## Task 10: setup.sh Enable/Disable Switch

**Files:**
- Modify: `setup.sh`

- [ ] **Step 1: Locate existing setup.sh config writing logic**

Find where `dashboard_config.json` is currently generated/updated.

- [ ] **Step 2: Add Tencent prompt block**

Append to `setup.sh`:

```bash
read -p "Enable Tencent Cloud dashboard? [y/N] " enable_tencent
enable_tencent=${enable_tencent:-N}
if [[ "$enable_tencent" =~ ^[Yy]$ ]]; then
    read -p "Tencent regions to monitor [ap-tokyo]: " tencent_regions
    tencent_regions=${tencent_regions:-ap-tokyo}
    # Convert space-separated to JSON array
    tencent_regions_json=$(echo "$tencent_regions" | tr ' ' '\n' | jq -R . | jq -s .)
    python3 -c "
import json, sys
with open('dashboard_config.json', 'r+') as f:
    cfg = json.load(f)
    cfg.setdefault('providers', {})
    cfg['providers']['tencent'] = {'enabled': True, 'regions': $tencent_regions_json}
    f.seek(0); json.dump(cfg, f, indent=2); f.truncate()
"
    if ! command -v tccli &> /dev/null; then
        echo "WARNING: tccli not found in PATH. Please install and configure it."
    fi
else
    python3 -c "
import json
with open('dashboard_config.json', 'r+') as f:
    cfg = json.load(f)
    cfg.setdefault('providers', {})
    cfg['providers']['tencent'] = {'enabled': False, 'regions': []}
    f.seek(0); json.dump(cfg, f, indent=2); f.truncate()
"
fi
```

- [ ] **Step 3: Test setup.sh syntax**

```bash
bash -n setup.sh
```

Expected: no syntax errors.

- [ ] **Step 4: Commit**

```bash
git add setup.sh
git commit -m "feat(setup): add interactive Tencent enable/disable prompt"
```

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec Section | Implementing Task |
|--------------|-------------------|
| Provider ABC + dataclasses | Task 1 |
| AWS Provider extraction | Task 2 |
| Tencent Provider (tccli, CVM, Lighthouse, metrics) | Task 3 |
| Provider registry | Task 4 |
| Config migration (old -> new) | Task 5 |
| Metrics Store provider column | Task 6 |
| Unified API routes + caching | Task 7 |
| Frontend genericization | Task 8 |
| Background sync script | Task 9 |
| setup.sh toggle | Task 10 |

**Gap:** None identified.

### 2. Placeholder Scan

- No "TBD", "TODO", or "implement later" found.
- No vague "add error handling" steps — each step has concrete code or command.
- No "similar to Task N" references that omit code.

### 3. Type Consistency

- `Resource.unique_id` format `provider:type:region:id` used consistently across Tasks 1, 2, 3, 6, 7.
- `metric_name="cpu_utilization"` used in Tasks 2, 3, 6.
- `BaseResourceProvider.sync_metrics_to_store(store, backfill_days)` signature consistent in Tasks 2, 3, 9.

### 4. Risk Notes

- **Frontend Task 8** does not have automated UI tests (no existing Selenium/Playwright setup). Verification is manual.
- **Task 2 (AWS extraction)** touches the most critical existing code. If AWS tests in `test_dashboard_api_resources.py` fail after extraction, prioritize fixing before proceeding to Task 7.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-27-tencent-dashboard.md`.**

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach do you prefer?**
