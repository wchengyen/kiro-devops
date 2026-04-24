# Resource Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Dashboard 新增 Resources 页面，自动发现 AWS EC2/RDS，展示 7 天 CPU sparkline，支持 pin 收藏。

**Architecture:** 后端新增 `dashboard/resources.py` 做 AWS 发现 + CloudWatch 查询 + 内存缓存；API 层暴露 `/resources` 和 `/resources/pins`；前端零构建 Vue 用纯 SVG 画 sparkline。

**Tech Stack:** Python 3.10, Flask, boto3, pytest, Vue 3 (global build), pure CSS

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `dashboard/resources.py` | Create | AWS 资源发现、CloudWatch 查询、内存缓存、数据聚合 |
| `tests/test_dashboard_resources.py` | Create | resources.py 单元测试（mock boto3） |
| `tests/test_dashboard_api_resources.py` | Create | API 路由集成测试 |
| `dashboard/config_store.py` | Modify | 新增 `pinned_resources` 读写方法 |
| `tests/test_dashboard_config_store.py` | Modify | 新增 pinned_resources 测试 |
| `dashboard/api.py` | Modify | 新增 `/resources` GET、`/resources/pins` GET/POST |
| `dashboard/static/index.html` | Modify | Sidebar 新增 Resources 导航入口 |
| `dashboard/static/app.js` | Modify | 新增 `ResourcesPage`、sparkline helper、pin 交互、路由 |
| `dashboard/static/style.css` | Modify | 资源表格、sparkline、pin 高亮、类型 badge 样式 |
| `.env.example` | Modify | 预留 `PROMETHEUS_URL` 配置项 |

---

### Task 1: 后端资源发现模块（EC2 + RDS）

**Files:**
- Create: `dashboard/resources.py`
- Create: `tests/test_dashboard_resources.py`

- [ ] **Step 1: 安装 boto3**

```bash
pip3 install boto3
```

- [ ] **Step 2: 创建 `dashboard/resources.py` 骨架**

```python
from dataclasses import dataclass, field


@dataclass
class Resource:
    id: str
    type: str
    name: str
    raw_id: str
    status: str
    meta: dict = field(default_factory=dict)
    sparkline: list = field(default_factory=list)
    current: float | None = None


def discover_ec2():
    try:
        import boto3
    except ImportError:
        return []
    client = boto3.client("ec2")
    resp = client.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running", "stopped"]}]
    )
    resources = []
    for reservation in resp.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            name = ""
            for tag in inst.get("Tags", []):
                if tag.get("Key") == "Name":
                    name = tag.get("Value", "")
                    break
            resources.append(
                Resource(
                    id=f"ec2:{inst['InstanceId']}",
                    type="ec2",
                    name=name or inst["InstanceId"],
                    raw_id=inst["InstanceId"],
                    status=inst["State"]["Name"],
                    meta={"instance_type": inst.get("InstanceType", "")},
                )
            )
    return resources


def discover_rds():
    try:
        import boto3
    except ImportError:
        return []
    client = boto3.client("rds")
    resp = client.describe_db_instances()
    resources = []
    for db in resp.get("DBInstances", []):
        resources.append(
            Resource(
                id=f"rds:{db['DBInstanceIdentifier']}",
                type="rds",
                name=db["DBInstanceIdentifier"],
                raw_id=db["DBInstanceIdentifier"],
                status=db["DBInstanceStatus"],
                meta={"engine": db.get("Engine", "")},
            )
        )
    return resources


def discover_all():
    return discover_ec2() + discover_rds()
```

- [ ] **Step 3: 写测试 `tests/test_dashboard_resources.py`**

```python
#!/usr/bin/env python3
"""Tests for dashboard resources discovery and metrics."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from dashboard.resources import Resource, discover_ec2, discover_rds, discover_all


@patch("dashboard.resources.boto3.client")
def test_discover_ec2_returns_instances(mock_client):
    mock_ec2 = MagicMock()
    mock_ec2.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-123",
                        "State": {"Name": "running"},
                        "InstanceType": "t3.micro",
                        "Tags": [{"Key": "Name", "Value": "test1"}],
                    }
                ]
            }
        ]
    }
    mock_client.return_value = mock_ec2

    result = discover_ec2()
    assert len(result) == 1
    assert result[0].id == "ec2:i-123"
    assert result[0].name == "test1"
    assert result[0].status == "running"
    assert result[0].meta["instance_type"] == "t3.micro"


@patch("dashboard.resources.boto3.client")
def test_discover_rds_returns_instances(mock_client):
    mock_rds = MagicMock()
    mock_rds.describe_db_instances.return_value = {
        "DBInstances": [
            {
                "DBInstanceIdentifier": "my-db",
                "DBInstanceStatus": "available",
                "Engine": "mysql",
            }
        ]
    }
    mock_client.return_value = mock_rds

    result = discover_rds()
    assert len(result) == 1
    assert result[0].id == "rds:my-db"
    assert result[0].status == "available"


@patch("dashboard.resources.boto3.client")
def test_discover_ec2_no_name_tag(mock_client):
    mock_ec2 = MagicMock()
    mock_ec2.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-456",
                        "State": {"Name": "stopped"},
                        "InstanceType": "t3.small",
                        "Tags": [],
                    }
                ]
            }
        ]
    }
    mock_client.return_value = mock_ec2

    result = discover_ec2()
    assert result[0].name == "i-456"
```

- [ ] **Step 4: 运行测试确认失败（boto3 mock 应正常通过）**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_resources.py -v
```

Expected: PASS（discover 函数已实现）

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/resources.py tests/test_dashboard_resources.py && git commit -m "feat(resources): add AWS EC2/RDS discovery module"
```

---

### Task 2: CloudWatch CPU 查询

**Files:**
- Modify: `dashboard/resources.py`
- Modify: `tests/test_dashboard_resources.py`

- [ ] **Step 1: 在 `dashboard/resources.py` 中新增 `get_cloudwatch_cpu`**

在文件末尾追加：

```python
import datetime


def get_cloudwatch_cpu(resource_id, namespace, dimension_name, days=7):
    try:
        import boto3
    except ImportError:
        return []
    client = boto3.client("cloudwatch")
    end = datetime.datetime.utcnow()
    start = end - datetime.timedelta(days=days)
    resp = client.get_metric_statistics(
        Namespace=namespace,
        MetricName="CPUUtilization",
        Dimensions=[{"Name": dimension_name, "Value": resource_id}],
        StartTime=start,
        EndTime=end,
        Period=86400,
        Statistics=["Average"],
    )
    points = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
    return [round(p["Average"], 1) for p in points]
```

- [ ] **Step 2: 在测试文件中新增 CloudWatch 测试**

```python
@patch("dashboard.resources.boto3.client")
def test_get_cloudwatch_cpu_returns_7_points(mock_client):
    mock_cw = MagicMock()
    mock_cw.get_metric_statistics.return_value = {
        "Datapoints": [
            {"Timestamp": datetime(2026, 4, 18), "Average": 10.5},
            {"Timestamp": datetime(2026, 4, 19), "Average": 20.0},
            {"Timestamp": datetime(2026, 4, 20), "Average": 15.2},
            {"Timestamp": datetime(2026, 4, 21), "Average": 30.1},
            {"Timestamp": datetime(2026, 4, 22), "Average": 25.0},
            {"Timestamp": datetime(2026, 4, 23), "Average": 18.3},
            {"Timestamp": datetime(2026, 4, 24), "Average": 22.7},
        ]
    }
    mock_client.return_value = mock_cw

    result = get_cloudwatch_cpu("i-123", "AWS/EC2", "InstanceId")
    assert len(result) == 7
    assert result[0] == 10.5
    assert result[-1] == 22.7


@patch("dashboard.resources.boto3.client")
def test_get_cloudwatch_cpu_returns_empty_without_boto3(mock_client):
    with patch.dict("sys.modules", {"boto3": None}):
        result = get_cloudwatch_cpu("i-123", "AWS/EC2", "InstanceId")
        assert result == []
```

注意测试文件顶部需要导入 `get_cloudwatch_cpu`：

```python
from dashboard.resources import (
    Resource,
    discover_ec2,
    discover_rds,
    discover_all,
    get_cloudwatch_cpu,
)
```

- [ ] **Step 3: 运行测试**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_resources.py -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/resources.py tests/test_dashboard_resources.py && git commit -m "feat(resources): add CloudWatch CPU query"
```

---

### Task 3: 缓存与聚合接口

**Files:**
- Modify: `dashboard/resources.py`
- Modify: `tests/test_dashboard_resources.py`

- [ ] **Step 1: 在 `dashboard/resources.py` 末尾追加缓存与聚合逻辑**

```python
import time

_cache = {"data": None, "ts": 0}
CACHE_TTL = 300


def get_all_resources_with_metrics(refresh=False):
    global _cache
    if (
        not refresh
        and _cache["data"] is not None
        and (time.time() - _cache["ts"]) < CACHE_TTL
    ):
        return _cache["data"]

    resources = discover_all()
    for r in resources:
        if r.type == "ec2":
            r.sparkline = get_cloudwatch_cpu(r.raw_id, "AWS/EC2", "InstanceId")
        elif r.type == "rds":
            r.sparkline = get_cloudwatch_cpu(
                r.raw_id, "AWS/RDS", "DBInstanceIdentifier"
            )
        if r.sparkline:
            r.current = r.sparkline[-1]

    data = {
        "resources": [resource_to_dict(r) for r in resources],
        "cached": False,
        "error": None,
    }
    _cache = {"data": data, "ts": time.time()}
    return data


def resource_to_dict(r: Resource) -> dict:
    return {
        "id": r.id,
        "type": r.type,
        "name": r.name,
        "raw_id": r.raw_id,
        "status": r.status,
        "meta": r.meta,
        "sparkline": r.sparkline,
        "current": r.current,
    }
```

- [ ] **Step 2: 在测试文件中新增聚合与缓存测试**

测试文件顶部导入新增：

```python
from dashboard.resources import (
    Resource,
    discover_ec2,
    discover_rds,
    discover_all,
    get_cloudwatch_cpu,
    get_all_resources_with_metrics,
)
```

追加测试：

```python
@patch("dashboard.resources.discover_all")
@patch("dashboard.resources.get_cloudwatch_cpu")
def test_get_all_resources_with_metrics(mock_cw, mock_discover):
    mock_discover.return_value = [
        Resource(
            id="ec2:i-123",
            type="ec2",
            name="test1",
            raw_id="i-123",
            status="running",
            meta={},
        )
    ]
    mock_cw.return_value = [10.0, 20.0, 30.0, 25.0, 15.0, 20.0, 22.0]

    result = get_all_resources_with_metrics(refresh=True)
    assert len(result["resources"]) == 1
    assert result["resources"][0]["sparkline"] == [10.0, 20.0, 30.0, 25.0, 15.0, 20.0, 22.0]
    assert result["resources"][0]["current"] == 22.0


@patch("dashboard.resources.discover_all")
@patch("dashboard.resources.get_cloudwatch_cpu")
def test_get_all_resources_uses_cache(mock_cw, mock_discover):
    mock_discover.return_value = [
        Resource(
            id="ec2:i-123",
            type="ec2",
            name="test1",
            raw_id="i-123",
            status="running",
            meta={},
        )
    ]
    mock_cw.return_value = [10.0, 20.0]

    result1 = get_all_resources_with_metrics(refresh=True)
    assert result1["resources"][0]["sparkline"] == [10.0, 20.0]

    mock_discover.reset_mock()
    mock_cw.reset_mock()
    result2 = get_all_resources_with_metrics(refresh=False)
    assert result2["resources"][0]["sparkline"] == [10.0, 20.0]
    mock_discover.assert_not_called()
    mock_cw.assert_not_called()
```

- [ ] **Step 3: 运行测试**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_resources.py -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/resources.py tests/test_dashboard_resources.py && git commit -m "feat(resources): add cache and metrics aggregation"
```

---

### Task 4: ConfigStore 扩展（pinned_resources）

**Files:**
- Modify: `dashboard/config_store.py`
- Modify: `tests/test_dashboard_config_store.py`

- [ ] **Step 1: 在 `dashboard/config_store.py` 末尾追加两个方法**

```python
    def read_pinned_resources(self) -> list[str]:
        data = self._read_dashboard_config()
        return data.get("pinned_resources", [])

    def write_pinned_resources(self, pins: list[str]) -> None:
        data = self._read_dashboard_config()
        data["pinned_resources"] = pins
        self._write_dashboard_config(data)
```

- [ ] **Step 2: 在 `tests/test_dashboard_config_store.py` 末尾追加测试**

```python
def test_pinned_resources_roundtrip(temp_mappings_file):
    store = ConfigStore(env_path="/nonexistent.env", mappings_path=temp_mappings_file)
    pins = ["ec2:i-123", "rds:my-db"]
    store.write_pinned_resources(pins)

    read_back = store.read_pinned_resources()
    assert read_back == pins


def test_read_pinned_resources_missing_file():
    store = ConfigStore(env_path="/nonexistent.env", mappings_path="/nonexistent.json")
    assert store.read_pinned_resources() == []


def test_read_pinned_resources_preserves_other_keys(temp_mappings_file):
    store = ConfigStore(env_path="/nonexistent.env", mappings_path=temp_mappings_file)
    store.write_mappings([{"alert_keyword": "cpu", "agent": "infra-agent"}])
    store.write_pinned_resources(["ec2:i-123"])

    assert store.read_mappings() == [{"alert_keyword": "cpu", "agent": "infra-agent"}]
    assert store.read_pinned_resources() == ["ec2:i-123"]
```

- [ ] **Step 3: 运行测试**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_config_store.py -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/config_store.py tests/test_dashboard_config_store.py && git commit -m "feat(config): add pinned_resources read/write"
```

---

### Task 5: API 路由（/resources + /resources/pins）

**Files:**
- Modify: `dashboard/api.py`
- Create: `tests/test_dashboard_api_resources.py`

- [ ] **Step 1: 在 `dashboard/api.py` 合适位置新增导入和路由**

在文件顶部导入区新增：

```python
from dashboard.resources import get_all_resources_with_metrics
```

在文件路由区新增（放在已有路由之后即可）：

```python
@dashboard_bp.route("/resources", methods=["GET"])
@require_auth
def get_resources():
    refresh = request.args.get("refresh") == "1"
    resource_type = request.args.get("type", "")
    try:
        data = get_all_resources_with_metrics(refresh=refresh)
        resources = data.get("resources", [])
        if resource_type:
            resources = [r for r in resources if r["type"] == resource_type]
        store = ConfigStore()
        pins = store.read_pinned_resources()
        return jsonify({
            "ok": True,
            "resources": resources,
            "pinned": pins,
            "cached": data.get("cached", False),
            "error": data.get("error"),
        })
    except Exception as e:
        return jsonify({"ok": True, "resources": [], "pinned": [], "error": str(e)}), 200


@dashboard_bp.route("/resources/pins", methods=["GET"])
@require_auth
def get_resource_pins():
    store = ConfigStore()
    return jsonify({"ok": True, "pins": store.read_pinned_resources()})


@dashboard_bp.route("/resources/pins", methods=["POST"])
@require_auth
def set_resource_pins():
    body = request.get_json(force=True) or {}
    pins = body.get("pins", [])
    store = ConfigStore()
    store.write_pinned_resources(pins)
    return jsonify({"ok": True})
```

- [ ] **Step 2: 创建 `tests/test_dashboard_api_resources.py`**

```python
#!/usr/bin/env python3
"""Tests for dashboard resources API routes."""

from unittest.mock import patch

import pytest
from flask import Flask

from dashboard import dashboard_bp, _sessions
import dashboard.api  # noqa: F401
from dashboard.config_store import ConfigStore


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("dashboard.DASHBOARD_TOKEN", "test-secret-token")
    _sessions.clear()
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    with app.test_client() as c:
        yield c
    _sessions.clear()


@pytest.fixture
def auth_client(client):
    resp = client.post("/api/dashboard/auth", json={"token": "test-secret-token"})
    assert resp.status_code == 200
    return client


@patch("dashboard.api.get_all_resources_with_metrics")
def test_get_resources(mock_get, auth_client):
    mock_get.return_value = {
        "resources": [
            {
                "id": "ec2:i-123",
                "type": "ec2",
                "name": "test1",
                "raw_id": "i-123",
                "status": "running",
                "meta": {},
                "sparkline": [10.0, 20.0],
                "current": 20.0,
            }
        ],
        "cached": False,
        "error": None,
    }
    resp = auth_client.get("/api/dashboard/resources")
    assert resp.status_code == 200
    assert resp.json["ok"] is True
    assert len(resp.json["resources"]) == 1
    assert resp.json["resources"][0]["id"] == "ec2:i-123"


@patch("dashboard.api.get_all_resources_with_metrics")
def test_get_resources_filter_by_type(mock_get, auth_client):
    mock_get.return_value = {
        "resources": [
            {"id": "ec2:i-123", "type": "ec2", "name": "test1", "raw_id": "i-123", "status": "running", "meta": {}, "sparkline": [], "current": None},
            {"id": "rds:my-db", "type": "rds", "name": "my-db", "raw_id": "my-db", "status": "available", "meta": {}, "sparkline": [], "current": None},
        ],
        "cached": False,
        "error": None,
    }
    resp = auth_client.get("/api/dashboard/resources?type=ec2")
    assert resp.status_code == 200
    assert len(resp.json["resources"]) == 1
    assert resp.json["resources"][0]["type"] == "ec2"


def test_get_pins(auth_client, monkeypatch, tmp_path):
    mappings_file = tmp_path / "dashboard_config.json"
    original_init = ConfigStore.__init__

    def patched_init(self, env_path=".env", mappings_path="dashboard_config.json"):
        original_init(self, env_path=env_path, mappings_path=str(mappings_file))

    monkeypatch.setattr("dashboard.api.ConfigStore.__init__", patched_init)

    resp = auth_client.get("/api/dashboard/resources/pins")
    assert resp.status_code == 200
    assert resp.json == {"ok": True, "pins": []}


def test_set_pins(auth_client, monkeypatch, tmp_path):
    mappings_file = tmp_path / "dashboard_config.json"
    original_init = ConfigStore.__init__

    def patched_init(self, env_path=".env", mappings_path="dashboard_config.json"):
        original_init(self, env_path=env_path, mappings_path=str(mappings_file))

    monkeypatch.setattr("dashboard.api.ConfigStore.__init__", patched_init)

    resp = auth_client.post("/api/dashboard/resources/pins", json={"pins": ["ec2:i-123"]})
    assert resp.status_code == 200
    assert resp.json == {"ok": True}

    resp = auth_client.get("/api/dashboard/resources/pins")
    assert resp.json["pins"] == ["ec2:i-123"]
```

- [ ] **Step 3: 运行全部 dashboard API 测试**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_api_resources.py tests/test_dashboard_api.py tests/test_dashboard_api_events.py tests/test_dashboard_api_scheduler.py -v
```

Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/api.py tests/test_dashboard_api_resources.py && git commit -m "feat(api): add /resources and /resources/pins routes"
```


---

### Task 6: 前端骨架（Sidebar + 路由 + 空页面）

**Files:**
- Modify: `dashboard/static/index.html`
- Modify: `dashboard/static/app.js`

- [ ] **Step 1: 修改 `dashboard/static/index.html`，在 Sidebar nav 中新增 Resources**

找到这一行：
```html
<router-link to="/config">Config</router-link>
```

在其前面插入：
```html
<router-link to="/resources">Resources</router-link>
```

- [ ] **Step 2: 修改 `dashboard/static/app.js`，新增 `ResourcesPage` 组件和路由**

在 `/* ---------- ConfigPage ---------- */` 之前插入 `ResourcesPage` 骨架：

```javascript
/* ---------- ResourcesPage ---------- */
const ResourcesPage = {
  template: `
    <div>
      <h2 class="page-title">Resources</h2>
      <p style="color:#94a3b8">加载中...</p>
    </div>
  `,
  setup() {
    return {};
  }
};
```

在 routes 数组中新增：

```javascript
const routes = [
  { path: "/login", component: LoginPage },
  { path: "/", component: OverviewPage },
  { path: "/agents", component: AgentsPage },
  { path: "/skills", component: SkillsPage },
  { path: "/events", component: EventsPage },
  { path: "/scheduler", component: SchedulerPage },
  { path: "/resources", component: ResourcesPage },
  { path: "/config", component: ConfigPage },
];
```

- [ ] **Step 3: 启动 Flask 应用，浏览器验证 `/dashboard/#/resources` 可访问且 Sidebar 有 Resources 入口**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -c "from app import app; app.run(port=5000, debug=False)" &
```

然后 curl 验证：
```bash
curl -s http://localhost:5000/dashboard/static/index.html | grep -o 'Resources'
```

Expected: 输出 `Resources`

- [ ] **Step 4: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/static/index.html dashboard/static/app.js && git commit -m "feat(dashboard): add Resources page skeleton and sidebar nav"
```

---

### Task 7: 前端表格与 Sparkline

**Files:**
- Modify: `dashboard/static/app.js`
- Modify: `dashboard/static/style.css`

- [ ] **Step 1: 将 `ResourcesPage` 骨架替换为完整表格 + sparkline 实现**

在 `app.js` 中找到 `ResourcesPage` 并完整替换为：

```javascript
/* ---------- ResourcesPage ---------- */
const ResourcesPage = {
  template: `
    <div>
      <h2 class="page-title">Resources</h2>
      <div class="toolbar">
        <button @click="load(true)" title="刷新">🔃</button>
        <select v-model="filterType" @change="load()">
          <option value="">全部类型</option>
          <option value="ec2">EC2</option>
          <option value="rds">RDS</option>
        </select>
        <input v-model="searchQ" placeholder="搜索 Name / ID" />
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;color:#64748b;cursor:pointer">
          <input type="checkbox" v-model="onlyPinned" /> 仅看 Pinned
        </label>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th style="width:40px">⭐</th>
              <th>Name</th>
              <th>Type</th>
              <th>ID</th>
              <th>Status</th>
              <th style="width:120px">7d Trend</th>
              <th style="width:70px">CPU</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in filteredResources" :key="r.id" :class="{ pinned: isPinned(r.id) }">
              <td><button class="pin-btn" @click="togglePin(r.id)">{{ isPinned(r.id) ? '★' : '☆' }}</button></td>
              <td>{{ r.name }}</td>
              <td><span :class="'badge badge-' + r.type">{{ r.type }}</span></td>
              <td><code class="tag">{{ r.raw_id }}</code></td>
              <td>{{ r.status }}</td>
              <td v-html="sparklineSvg(r.sparkline, sparklineColor(r.type))"></td>
              <td>{{ r.current != null ? r.current + '%' : '-' }}</td>
            </tr>
            <tr v-if="filteredResources.length === 0"><td colspan="7" class="empty">暂无数据</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  `,
  setup() {
    const resources = ref([]);
    const pins = ref([]);
    const filterType = ref("");
    const searchQ = ref("");
    const onlyPinned = ref(false);

    function isPinned(id) { return pins.value.includes(id); }
    async function togglePin(id) {
      const idx = pins.value.indexOf(id);
      if (idx >= 0) pins.value.splice(idx, 1);
      else pins.value.push(id);
      await api("/resources/pins", { method: "POST", body: { pins: pins.value } });
      reorder();
    }
    function reorder() {
      resources.value.sort((a, b) => {
        const pa = isPinned(a.id) ? -1 : 1;
        const pb = isPinned(b.id) ? -1 : 1;
        return pa - pb;
      });
    }
    function sparklineColor(type) {
      return { ec2: "#3b82f6", rds: "#8b5cf6", eks: "#f59e0b" }[type] || "#94a3b8";
    }
    function sparklineSvg(points, color) {
      if (!points || points.length < 2) return '<span style="color:#cbd5e1">-</span>';
      const valid = points.filter(v => v != null);
      if (valid.length < 2) return '<span style="color:#cbd5e1">-</span>';
      const min = Math.min(...valid), max = Math.max(...valid);
      const range = max - min || 1;
      const pts = points.map((v, i) => {
        if (v == null) return "";
        const x = (i / (points.length - 1)) * 100;
        const y = 30 - ((v - min) / range) * 30;
        return `${x},${y}`;
      }).filter(Boolean).join(" ");
      return `<svg viewBox="0 0 100 30" width="100" height="30" style="display:block"><polyline fill="none" stroke="${color}" stroke-width="2" points="${pts}"/></svg>`;
    }
    async function load(refresh = false) {
      const qs = new URLSearchParams();
      if (refresh) qs.append("refresh", "1");
      if (filterType.value) qs.append("type", filterType.value);
      const data = await api("/resources?" + qs.toString());
      resources.value = data.resources || [];
      pins.value = data.pinned || [];
      reorder();
    }
    const filteredResources = computed(() => {
      let list = resources.value;
      if (onlyPinned.value) list = list.filter(r => isPinned(r.id));
      const q = searchQ.value.trim().toLowerCase();
      if (q) list = list.filter(r => (r.name + r.raw_id).toLowerCase().includes(q));
      return list;
    });

    onMounted(() => load());
    return { resources, pins, filterType, searchQ, onlyPinned, isPinned, togglePin, sparklineSvg, sparklineColor, filteredResources, load };
  }
};
```

- [ ] **Step 2: 在 `dashboard/static/style.css` 末尾追加 Resources 专用样式**

```css
/* ===== Resources Page ===== */
tr.pinned td { background: #f8fafc; }
tr.pinned td:first-child { border-left: 3px solid #f59e0b; }
.pin-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 16px;
  padding: 0;
  color: #cbd5e1;
  transition: .15s;
}
.pin-btn:hover { color: #f59e0b; }
tr.pinned .pin-btn { color: #f59e0b; }
.badge-ec2 { background: #eff6ff; color: #2563eb; }
.badge-rds { background: #f5f3ff; color: #7c3aed; }
.badge-eks { background: #fff7ed; color: #c2410c; }
```

- [ ] **Step 3: 浏览器验证表格渲染**

由于后端需要 AWS 凭证才能返回真实数据，先用 mock API 验证前端渲染。可以在浏览器控制台手动测试 `sparklineSvg` 函数：

```javascript
sparklineSvg([10,20,30,25,15,20,22], "#3b82f6")
```

Expected: 返回一段包含 `<svg>` 和 `<polyline>` 的 HTML 字符串。

- [ ] **Step 4: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/static/app.js dashboard/static/style.css && git commit -m "feat(dashboard): add Resources table with SVG sparkline"
```

---

### Task 8: Pin 交互与过滤打磨

**Files:**
- Modify: `dashboard/static/app.js`
- Modify: `dashboard/static/style.css`

Task 7 中已经包含了 pin toggle 和过滤的基础逻辑，本任务负责打磨交互细节。

- [ ] **Step 1: 确认 `app.js` 中 `togglePin` 和 `reorder` 逻辑已生效**

`togglePin` 已经：
1. 修改本地 `pins.value`
2. 调 POST `/resources/pins` 持久化
3. 调 `reorder()` 立即重排表格

`filteredResources` computed 已经支持：
- `onlyPinned` 过滤
- `searchQ` 搜索

无需额外代码修改，本步骤主要是验证。

- [ ] **Step 2: 在 `style.css` 中微调 pin 高亮和表格行 hover 的共存**

确保 `tr.pinned:hover td` 也有合适的背景。在 style.css 的 Resources 区域追加：

```css
tr.pinned:hover td { background: #f1f5f9; }
```

- [ ] **Step 3: 浏览器验证 pin 交互**

1. 打开 `/dashboard/#/resources`
2. 点击某行的 ☆ → 应变为 ★，该行置顶，左侧出现金色竖条
3. 勾选"仅看 Pinned" → 只显示 pinned 行
4. 刷新页面 → pinned 状态应保留

- [ ] **Step 4: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/static/app.js dashboard/static/style.css && git commit -m "style(dashboard): polish pin interaction and hover states"
```

---

### Task 9: 配置与回归测试

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: 修改 `.env.example`，在末尾预留 Prometheus 配置**

```bash
# === Prometheus 配置（Resource Dashboard 预留）===
# PROMETHEUS_URL=http://localhost:9090
```

- [ ] **Step 2: 运行全部 dashboard 回归测试**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard*.py -q
```

Expected: ALL PASS（当前 baseline 为 24 个测试，新增后应全部通过）

- [ ] **Step 3: Commit 并推送**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add .env.example && git commit -m "chore: add PROMETHEUS_URL placeholder to .env.example"
```

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec 要求 | 对应 Task |
|-----------|-----------|
| 后端资源发现 EC2/RDS | Task 1 |
| CloudWatch CPU 查询 | Task 2 |
| 内存缓存 TTL 5 分钟 | Task 3 |
| Pin 状态持久化 | Task 4 |
| `/resources` GET + type 过滤 | Task 5 |
| `/resources/pins` GET/POST | Task 5 |
| Sidebar 新增入口 | Task 6 |
| Sparkline 纯 SVG | Task 7 |
| Pin 交互 + 过滤 | Task 7-8 |
| `.env.example` 预留 | Task 9 |

**Gap**: EKS + Prometheus 查询在 Spec 中明确列为"第二阶段"，不在本 plan 范围内。

### 2. Placeholder Scan

- 无 "TBD", "TODO", "implement later"
- 无 "Add appropriate error handling" 等模糊描述
- 每个 step 包含具体代码或命令

### 3. Type Consistency

- `Resource` dataclass 字段名与 `resource_to_dict()` 输出键名一致
- API 返回的 JSON 键名与前端 `app.js` 中访问的属性名一致（`r.id`, `r.name`, `r.sparkline`, `r.current`）
- Pin ID 格式 `ec2:i-xxx` / `rds:xxx` 在后端、ConfigStore、前端中统一

### 4. Scope Check

- 本 plan 聚焦 MVP：EC2/RDS + CloudWatch + pin + sparkline
- EKS + Prometheus 作为预留架构，但不实现
- 无内存/磁盘/网络等其他指标

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-24-resource-dashboard.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
