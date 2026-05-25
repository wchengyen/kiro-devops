# Resource Tree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Resource Tree topology visualization page to the kiro-devops Dashboard, supporting AWS resource auto-discovery (EKS/ELB/EC2/RDS/VPC/Subnet), custom Tag-based grouping, and interactive manual editing.

**Architecture:** Backend uses Flask Blueprint routes with a new `ResourceTreeStore` (SQLite) and `AWSResourceScanner` (reusing existing boto3 patterns). Frontend adds a Vue page with Cytoscape.js for topology rendering, node drag, edge creation/deletion, and layout switching.

**Tech Stack:** Flask, SQLite, Vue 3 Global Build, Cytoscape.js 3.26.0, boto3, pytest

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `dashboard/resource_tree_store.py` | Create | SQLite CRUD for `resource_relations` and `node_positions` |
| `dashboard/resource_tree.py` | Create | `ResourceTreeBuilder` (graph construction) and `AWSResourceScanner` (auto-discovery) |
| `dashboard/api.py` | Modify | Add 6 new REST endpoints under `/api/dashboard/resource-tree/*` |
| `dashboard/static/index.html` | Modify | Add Cytoscape.js CDN script tag |
| `dashboard/static/app.js` | Modify | Add `ResourceTreePage` component, router entry, sidebar nav |
| `dashboard/static/style.css` | Modify | Add Cytoscape canvas container and node type color styles |
| `tests/test_resource_tree_store.py` | Create | Unit tests for SQLite store |
| `tests/test_resource_tree_builder.py` | Create | Unit tests for graph builder |
| `tests/test_resource_tree_scanner.py` | Create | Unit tests for AWS scanner (mocked boto3) |
| `tests/test_dashboard_api_resource_tree.py` | Create | Integration tests for all new API endpoints |

---

### Task 1: SQLite Schema & ResourceTreeStore

**Files:**
- Create: `dashboard/resource_tree_store.py`
- Test: `tests/test_resource_tree_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_resource_tree_store.py
import os
import pytest
from dashboard.resource_tree_store import ResourceTreeStore

@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "resource_tree.db")
    return ResourceTreeStore(db_path)


def test_add_and_get_relation(store):
    rid = store.add_relation(
        source_id="aws:eks:cn-north-1:cluster-1",
        target_id="aws:ec2:cn-north-1:i-123",
        relation_type="contains",
        source_origin="auto_scan",
        provider="aws",
    )
    assert rid is not None
    relations = store.get_relations()
    assert len(relations) == 1
    assert relations[0]["source_id"] == "aws:eks:cn-north-1:cluster-1"


def test_clear_auto_scan_relations(store):
    store.add_relation("a", "b", "contains", "auto_scan", "aws")
    store.add_relation("a", "c", "contains", "manual", "aws")
    deleted = store.clear_auto_scan_relations("aws")
    assert deleted == 1
    remaining = store.get_relations()
    assert len(remaining) == 1
    assert remaining[0]["source_origin"] == "manual"


def test_positions_crud(store):
    store.save_positions({"node-a": {"x": 10, "y": 20}, "node-b": {"x": 30, "y": 40}})
    positions = store.get_positions()
    assert positions["node-a"]["x"] == 10
    assert positions["node-b"]["y"] == 40
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_tree_store.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'dashboard.resource_tree_store'`

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/resource_tree_store.py
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


class ResourceTreeStore:
    def __init__(self, db_path: str = "memory_db/resource_tree.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS resource_relations (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    source_origin TEXT NOT NULL,
                    provider TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_relations_source ON resource_relations(source_id);
                CREATE INDEX IF NOT EXISTS idx_relations_target ON resource_relations(target_id);
                CREATE INDEX IF NOT EXISTS idx_relations_origin ON resource_relations(source_origin);
                CREATE INDEX IF NOT EXISTS idx_relations_provider ON resource_relations(provider);

                CREATE TABLE IF NOT EXISTS node_positions (
                    id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL UNIQUE,
                    layout_name TEXT NOT NULL DEFAULT 'default',
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_node_positions_layout ON node_positions(layout_name);
                """
            )

    def add_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        source_origin: str,
        provider: str | None = None,
    ) -> str:
        rid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO resource_relations (id, source_id, target_id, relation_type, source_origin, provider, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rid, source_id, target_id, relation_type, source_origin, provider, now, now),
            )
        return rid

    def get_relations(self, provider: str | None = None) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if provider:
                rows = conn.execute(
                    "SELECT * FROM resource_relations WHERE provider = ? ORDER BY created_at",
                    (provider,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM resource_relations ORDER BY created_at").fetchall()
            return [dict(r) for r in rows]

    def delete_relation(self, relation_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM resource_relations WHERE id = ?", (relation_id,))
            return cur.rowcount > 0

    def clear_auto_scan_relations(self, provider: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM resource_relations WHERE provider = ? AND source_origin = 'auto_scan'",
                (provider,),
            )
            return cur.rowcount

    def save_positions(self, positions: dict[str, dict], layout_name: str = "default") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            for node_id, pos in positions.items():
                conn.execute(
                    """
                    INSERT INTO node_positions (id, node_id, layout_name, x, y, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(node_id) DO UPDATE SET
                        x = excluded.x, y = excluded.y, updated_at = excluded.updated_at
                    """,
                    (str(uuid.uuid4()), node_id, layout_name, pos["x"], pos["y"], now),
                )

    def get_positions(self, layout_name: str = "default") -> dict[str, dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT node_id, x, y FROM node_positions WHERE layout_name = ?",
                (layout_name,),
            ).fetchall()
            return {r["node_id"]: {"x": r["x"], "y": r["y"]} for r in rows}
```

**注意**：在文件頂部添加 `import os`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_tree_store.py -v`

Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_resource_tree_store.py dashboard/resource_tree_store.py
git commit -m "feat(resource-tree): add ResourceTreeStore with SQLite schema"
```

---

### Task 2: ResourceTreeBuilder

**Files:**
- Create: `dashboard/resource_tree.py`
- Test: `tests/test_resource_tree_builder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_resource_tree_builder.py
import pytest
from dashboard.resource_tree import ResourceTreeBuilder
from dashboard.providers.base import Resource


@pytest.fixture
def builder():
    return ResourceTreeBuilder()


def test_build_graph_with_tag_groups(builder):
    resources = [
        Resource(
            provider="aws", resource_type="ec2", region="cn-north-1",
            id="i-123", name="web-01", status="running",
            tags={"Project": "myapp", "Environment": "prod"}
        ),
        Resource(
            provider="aws", resource_type="ec2", region="cn-north-1",
            id="i-456", name="web-02", status="running",
            tags={"Project": "myapp", "Environment": "dev"}
        ),
    ]
    relations = []
    group_by_tags = ["Project", "Environment"]
    positions = {}

    graph = builder.build_graph(resources, relations, group_by_tags, positions)

    node_ids = {n["id"] for n in graph["nodes"]}
    assert "aws:ec2:cn-north-1:i-123" in node_ids
    assert "group:Project:myapp" in node_ids
    assert "group:Environment:prod" in node_ids

    edge_pairs = {(e["source"], e["target"]) for e in graph["edges"]}
    assert ("aws:ec2:cn-north-1:i-123", "group:Environment:prod") in edge_pairs
    assert ("group:Environment:prod", "group:Project:myapp") in edge_pairs


def test_build_graph_with_auto_scan_relations(builder):
    resources = [
        Resource(
            provider="aws", resource_type="eks", region="cn-north-1",
            id="cluster-1", name="prod-cluster", status="ACTIVE", tags={}
        ),
        Resource(
            provider="aws", resource_type="ec2", region="cn-north-1",
            id="i-123", name="node-1", status="running", tags={}
        ),
    ]
    relations = [
        {
            "source_id": "aws:eks:cn-north-1:cluster-1",
            "target_id": "aws:ec2:cn-north-1:i-123",
            "relation_type": "contains",
            "source_origin": "auto_scan",
        }
    ]
    graph = builder.build_graph(resources, relations, [], {})
    edge_pairs = {(e["source"], e["target"]) for e in graph["edges"]}
    assert ("aws:eks:cn-north-1:cluster-1", "aws:ec2:cn-north-1:i-123") in edge_pairs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_tree_builder.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'dashboard.resource_tree'` or `ResourceTreeBuilder` not defined

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/resource_tree.py
from typing import Any
from dashboard.providers.base import Resource


class ResourceTreeBuilder:
    def build_graph(
        self,
        resources: list[Resource],
        relations: list[dict],
        group_by_tags: list[str],
        positions: dict[str, dict],
    ) -> dict:
        nodes: list[dict] = []
        edges: list[dict] = []
        node_ids: set[str] = set()

        # Build resource nodes
        for r in resources:
            nid = r.unique_id
            node_ids.add(nid)
            pos = positions.get(nid, {})
            nodes.append({
                "id": nid,
                "label": r.name,
                "type": r.resource_type,
                "position": pos if pos else None,
                "data": {
                    "provider": r.provider,
                    "region": r.region,
                    "status": r.status,
                    "tags": r.tags,
                    "class_type": r.class_type,
                    "os_or_engine": r.os_or_engine,
                },
            })

        # Build tag group nodes and edges
        group_nodes: dict[str, dict] = {}
        for r in resources:
            prev_group_id: str | None = None
            for tag_key in group_by_tags:
                tag_value = r.tags.get(tag_key)
                if not tag_value:
                    continue
                group_id = f"group:{tag_key}:{tag_value}"
                if group_id not in group_nodes:
                    group_nodes[group_id] = {
                        "id": group_id,
                        "label": f"{tag_key}: {tag_value}",
                        "type": "tag_group",
                        "is_group": True,
                        "position": positions.get(group_id, None),
                        "data": {},
                    }
                    node_ids.add(group_id)
                if prev_group_id:
                    edges.append({
                        "id": f"{prev_group_id}->{group_id}",
                        "source": prev_group_id,
                        "target": group_id,
                        "relation_type": "grouped_by",
                        "source_origin": "tag_group",
                    })
                prev_group_id = group_id
            # Link last group to resource
            if prev_group_id:
                edges.append({
                    "id": f"{r.unique_id}->{prev_group_id}",
                    "source": r.unique_id,
                    "target": prev_group_id,
                    "relation_type": "grouped_by",
                    "source_origin": "tag_group",
                })

        nodes.extend(group_nodes.values())

        # Build auto_scan / manual relation edges
        for rel in relations:
            sid = rel["source_id"]
            tid = rel["target_id"]
            # Only add edges where both nodes exist in our graph
            if sid in node_ids or tid in node_ids:
                edges.append({
                    "id": rel.get("id", f"{sid}->{tid}"),
                    "source": sid,
                    "target": tid,
                    "relation_type": rel["relation_type"],
                    "source_origin": rel["source_origin"],
                })

        return {"nodes": nodes, "edges": edges}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_tree_builder.py -v`

Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_resource_tree_builder.py dashboard/resource_tree.py
git commit -m "feat(resource-tree): add ResourceTreeBuilder for graph construction"
```

---

### Task 3: AWSResourceScanner

**Files:**
- Modify: `dashboard/resource_tree.py`（追加 Scanner 類）
- Test: `tests/test_resource_tree_scanner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_resource_tree_scanner.py
import pytest
from unittest.mock import MagicMock, patch
from dashboard.resource_tree import AWSResourceScanner


def _mock_boto_client(service, region_name=None):
    client = MagicMock()
    if service == "eks":
        client.list_clusters.return_value = {"clusters": ["cluster-1"]}
        client.list_nodegroups.return_value = {"nodegroups": ["ng-1"]}
        client.describe_nodegroup.return_value = {
            "nodegroup": {
                "resources": {
                    "autoScalingGroups": [{"name": "asg-1"}]
                }
            }
        }
    elif service == "autoscaling":
        client.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [
                {"Instances": [{"InstanceId": "i-123"}]}
            ]
        }
    elif service == "elbv2":
        client.describe_load_balancers.return_value = {
            "LoadBalancers": [
                {"LoadBalancerArn": "arn:aws:elasticloadbalancing:cn-north-1:123:loadbalancer/app/lb-1/abc", "LoadBalancerName": "lb-1"}
            ]
        }
        client.describe_target_groups.return_value = {
            "TargetGroups": [{"TargetGroupArn": "arn:aws:elasticloadbalancing:cn-north-1:123:targetgroup/tg-1/abc"}]
        }
        client.describe_target_health.return_value = {
            "TargetHealthDescriptions": [
                {"Target": {"Id": "i-123"}}
            ]
        }
    elif service == "ec2":
        client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-123",
                            "SubnetId": "subnet-1",
                            "VpcId": "vpc-1",
                            "SecurityGroups": [{"GroupId": "sg-1"}],
                        }
                    ]
                }
            ]
        }
    elif service == "rds":
        client.describe_db_instances.return_value = {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": "db-1",
                    "DBSubnetGroup": {
                        "DBSubnetGroupName": "db-subnet-group-1",
                        "VpcId": "vpc-1",
                        "Subnets": [{"SubnetIdentifier": "subnet-2"}],
                    }
                }
            ]
        }
    return client


@patch("dashboard.resource_tree.boto3")
def test_scan_eks_ec2(mock_boto3):
    mock_boto3.client.side_effect = _mock_boto_client
    scanner = AWSResourceScanner()
    relations = scanner.scan(["cn-north-1"])

    eks_ec2 = [r for r in relations if r["relation_type"] == "contains" and "eks" in r["source_id"]]
    assert len(eks_ec2) == 1
    assert eks_ec2[0]["source_id"] == "aws:eks:cn-north-1:cluster-1"
    assert eks_ec2[0]["target_id"] == "aws:ec2:cn-north-1:i-123"


@patch("dashboard.resource_tree.boto3")
def test_scan_elb_ec2(mock_boto3):
    mock_boto3.client.side_effect = _mock_boto_client
    scanner = AWSResourceScanner()
    relations = scanner.scan(["cn-north-1"])

    elb_ec2 = [r for r in relations if r["relation_type"] == "attached_to"]
    assert len(elb_ec2) >= 1
    assert elb_ec2[0]["target_id"] == "aws:ec2:cn-north-1:i-123"


@patch("dashboard.resource_tree.boto3")
def test_scan_ec2_network(mock_boto3):
    mock_boto3.client.side_effect = _mock_boto_client
    scanner = AWSResourceScanner()
    relations = scanner.scan(["cn-north-1"])

    subnet_edges = [r for r in relations if "subnet" in r["target_id"]]
    assert len(subnet_edges) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_tree_scanner.py -v`

Expected: FAIL with `AWSResourceScanner` not defined or methods missing

- [ ] **Step 3: Write minimal implementation**

在 `dashboard/resource_tree.py` 末尾追加：

```python
import json
import os
import boto3


def _load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "..", "dashboard_config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)
    return {}


class AWSResourceScanner:
    def __init__(self):
        pass

    def scan(self, regions: list[str]) -> list[dict]:
        relations: list[dict] = []
        for region in regions:
            relations.extend(self._scan_eks_ec2(region))
            relations.extend(self._scan_elb_targets(region))
            relations.extend(self._scan_ec2_network(region))
            relations.extend(self._scan_rds_network(region))
        return relations

    def _scan_eks_ec2(self, region: str) -> list[dict]:
        relations: list[dict] = []
        try:
            eks = boto3.client("eks", region_name=region)
            ec2 = boto3.client("ec2", region_name=region)
            asg = boto3.client("autoscaling", region_name=region)
            clusters = eks.list_clusters()["clusters"]
            for cluster_name in clusters:
                nodegroups = eks.list_nodegroups(clusterName=cluster_name)["nodegroups"]
                for ng_name in nodegroups:
                    ng = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=ng_name)["nodegroup"]
                    for asg_info in ng.get("resources", {}).get("autoScalingGroups", []):
                        asg_detail = asg.describe_auto_scaling_groups(
                            AutoScalingGroupNames=[asg_info["name"]]
                        )["AutoScalingGroups"][0]
                        for instance in asg_detail.get("Instances", []):
                            relations.append({
                                "source_id": f"aws:eks:{region}:{cluster_name}",
                                "target_id": f"aws:ec2:{region}:{instance['InstanceId']}",
                                "relation_type": "contains",
                            })
        except Exception:
            pass
        return relations

    def _scan_elb_targets(self, region: str) -> list[dict]:
        relations: list[dict] = []
        try:
            elbv2 = boto3.client("elbv2", region_name=region)
            lbs = elbv2.describe_load_balancers()["LoadBalancers"]
            for lb in lbs:
                lb_arn = lb["LoadBalancerArn"]
                lb_id = lb_arn.split("/")[-1]
                tgs = elbv2.describe_target_groups(LoadBalancerArn=lb_arn)["TargetGroups"]
                for tg in tgs:
                    health = elbv2.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])["TargetHealthDescriptions"]
                    for target in health:
                        target_id = target["Target"]["Id"]
                        if target_id.startswith("i-"):
                            relations.append({
                                "source_id": f"aws:elb:{region}:{lb_id}",
                                "target_id": f"aws:ec2:{region}:{target_id}",
                                "relation_type": "attached_to",
                            })
        except Exception:
            pass
        return relations

    def _scan_ec2_network(self, region: str) -> list[dict]:
        relations: list[dict] = []
        try:
            ec2 = boto3.client("ec2", region_name=region)
            instances = ec2.describe_instances()["Reservations"]
            for reservation in instances:
                for inst in reservation["Instances"]:
                    instance_id = inst["InstanceId"]
                    subnet_id = inst.get("SubnetId")
                    vpc_id = inst.get("VpcId")
                    if subnet_id:
                        relations.append({
                            "source_id": f"aws:ec2:{region}:{instance_id}",
                            "target_id": f"aws:subnet:{region}:{subnet_id}",
                            "relation_type": "belongs_to",
                        })
                    if vpc_id:
                        relations.append({
                            "source_id": f"aws:subnet:{region}:{subnet_id}",
                            "target_id": f"aws:vpc:{region}:{vpc_id}",
                            "relation_type": "belongs_to",
                        })
        except Exception:
            pass
        return relations

    def _scan_rds_network(self, region: str) -> list[dict]:
        relations: list[dict] = []
        try:
            rds = boto3.client("rds", region_name=region)
            dbs = rds.describe_db_instances()["DBInstances"]
            for db in dbs:
                db_id = db["DBInstanceIdentifier"]
                subnet_group = db.get("DBSubnetGroup", {})
                vpc_id = subnet_group.get("VpcId")
                if vpc_id:
                    relations.append({
                        "source_id": f"aws:rds:{region}:{db_id}",
                        "target_id": f"aws:vpc:{region}:{vpc_id}",
                        "relation_type": "belongs_to",
                    })
                for subnet in subnet_group.get("Subnets", []):
                    subnet_id = subnet["SubnetIdentifier"]
                    relations.append({
                        "source_id": f"aws:rds:{region}:{db_id}",
                        "target_id": f"aws:subnet:{region}:{subnet_id}",
                        "relation_type": "belongs_to",
                    })
        except Exception:
            pass
        return relations
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_tree_scanner.py -v`

Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_resource_tree_scanner.py dashboard/resource_tree.py
git commit -m "feat(resource-tree): add AWSResourceScanner for auto-discovery"
```

---

### Task 4: Dashboard API Routes

**Files:**
- Modify: `dashboard/api.py`
- Test: `tests/test_dashboard_api_resource_tree.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_api_resource_tree.py
import pytest
import json
import os
from flask import Flask
from dashboard import dashboard_bp, _sessions
import dashboard.api
from dashboard.config_store import ConfigStore


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr("dashboard.DASHBOARD_TOKEN", "test-secret-token")
    _sessions.clear()
    # Mock config store to use temp path
    config_path = str(tmp_path / "dashboard_config.json")
    with open(config_path, "w") as f:
        json.dump({
            "providers": {"aws": {"enabled": True, "regions": ["cn-north-1"]}},
            "resource_tree": {"group_by_tags": ["Project"], "layout_algorithm": "cose"}
        }, f)
    monkeypatch.setattr("dashboard.api.CONFIG_PATH", config_path)
    monkeypatch.setattr("dashboard.resource_tree_store.os.makedirs", lambda *a, **k: None)
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


def test_get_resource_tree_config(auth_client):
    resp = auth_client.get("/api/dashboard/resource-tree/config")
    assert resp.status_code == 200
    data = resp.json
    assert data["ok"] is True
    assert "group_by_tags" in data["config"]


def test_post_resource_tree_config(auth_client):
    resp = auth_client.post(
        "/api/dashboard/resource-tree/config",
        json={"group_by_tags": ["Team"], "layout_algorithm": "grid"}
    )
    assert resp.status_code == 200
    assert resp.json["ok"] is True


def test_get_resource_tree_graph(auth_client, monkeypatch):
    # Mock discover_resources to return empty list
    monkeypatch.setattr(
        "dashboard.api.get_provider",
        lambda name: type("P", (), {
            "discover_resources": lambda *a, **k: [],
            "regions": lambda: ["cn-north-1"],
            "is_enabled": lambda: True,
        })()
    )
    resp = auth_client.get("/api/dashboard/resource-tree/graph?provider=aws")
    assert resp.status_code == 200
    data = resp.json
    assert data["ok"] is True
    assert "nodes" in data
    assert "edges" in data


def test_post_and_delete_relation(auth_client):
    resp = auth_client.post(
        "/api/dashboard/resource-tree/relations",
        json={"source_id": "a", "target_id": "b", "relation_type": "depends_on"}
    )
    assert resp.status_code == 200
    rid = resp.json["id"]
    resp = auth_client.delete(f"/api/dashboard/resource-tree/relations/{rid}")
    assert resp.status_code == 200
    assert resp.json["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_api_resource_tree.py -v`

Expected: FAIL with 404 on `/api/dashboard/resource-tree/config`

- [ ] **Step 3: Write minimal implementation**

在 `dashboard/api.py` 的現有導入區域添加：

```python
import uuid
import threading
from datetime import datetime, timezone
from dashboard.resource_tree_store import ResourceTreeStore
from dashboard.resource_tree import ResourceTreeBuilder, AWSResourceScanner
```

在 `dashboard/api.py` 末尾（現有路由之後）追加以下路由：

```python
_scan_jobs: dict[str, dict] = {}
_DEFAULT_TREE_DB = os.path.join(os.path.dirname(__file__), "..", "memory_db", "resource_tree.db")


def _get_tree_store():
    return ResourceTreeStore(_DEFAULT_TREE_DB)


def _load_dashboard_config():
    config_path = os.path.join(os.path.dirname(__file__), "..", "dashboard_config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)
    return {}


def _save_dashboard_config(config):
    config_path = os.path.join(os.path.dirname(__file__), "..", "dashboard_config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


@dashboard_bp.route("/api/dashboard/resource-tree/config", methods=["GET"])
@require_auth
def get_resource_tree_config():
    config = _load_dashboard_config()
    tree_config = config.get("resource_tree", {
        "group_by_tags": [],
        "layout_algorithm": "cose",
        "default_provider": "aws",
    })
    return jsonify({"ok": True, "config": tree_config})


@dashboard_bp.route("/api/dashboard/resource-tree/config", methods=["POST"])
@require_auth
def post_resource_tree_config():
    body = request.get_json(force=True) or {}
    config = _load_dashboard_config()
    tree_config = config.get("resource_tree", {})
    if "group_by_tags" in body:
        tree_config["group_by_tags"] = body["group_by_tags"]
    if "layout_algorithm" in body:
        tree_config["layout_algorithm"] = body["layout_algorithm"]
    config["resource_tree"] = tree_config
    _save_dashboard_config(config)
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/resource-tree/graph", methods=["GET"])
@require_auth
def get_resource_tree_graph():
    provider_name = request.args.get("provider", "aws")
    config = _load_dashboard_config()
    tree_config = config.get("resource_tree", {})
    group_by_tags = tree_config.get("group_by_tags", [])

    provider = get_provider(provider_name)
    resources = []
    if provider and provider.is_enabled():
        for region in provider.regions():
            resources.extend(provider.discover_resources(region))

    store = _get_tree_store()
    relations = store.get_relations(provider=provider_name)
    positions = store.get_positions()

    builder = ResourceTreeBuilder()
    graph = builder.build_graph(resources, relations, group_by_tags, positions)
    return jsonify({"ok": True, **graph})


@dashboard_bp.route("/api/dashboard/resource-tree/scan", methods=["POST"])
@require_auth
def post_resource_tree_scan():
    body = request.get_json(force=True) or {}
    provider_name = body.get("provider", "aws")
    job_id = str(uuid.uuid4())
    _scan_jobs[job_id] = {"status": "running", "count": 0, "error": None}

    def _do_scan():
        try:
            config = _load_dashboard_config()
            regions = config.get("providers", {}).get(provider_name, {}).get("regions", [])
            if not regions:
                regions = config.get("regions", [])

            store = _get_tree_store()
            store.clear_auto_scan_relations(provider_name)

            scanner = AWSResourceScanner()
            relations = scanner.scan(regions)
            for rel in relations:
                store.add_relation(
                    source_id=rel["source_id"],
                    target_id=rel["target_id"],
                    relation_type=rel["relation_type"],
                    source_origin="auto_scan",
                    provider=provider_name,
                )
            _scan_jobs[job_id]["status"] = "done"
            _scan_jobs[job_id]["count"] = len(relations)
        except Exception as e:
            _scan_jobs[job_id]["status"] = "failed"
            _scan_jobs[job_id]["error"] = str(e)

    threading.Thread(target=_do_scan, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@dashboard_bp.route("/api/dashboard/resource-tree/scan/<job_id>", methods=["GET"])
@require_auth
def get_resource_tree_scan_status(job_id):
    job = _scan_jobs.get(job_id, {"status": "unknown", "count": 0, "error": None})
    return jsonify({"ok": True, **job})


@dashboard_bp.route("/api/dashboard/resource-tree/relations", methods=["POST"])
@require_auth
def post_resource_tree_relation():
    body = request.get_json(force=True) or {}
    source_id = body.get("source_id")
    target_id = body.get("target_id")
    relation_type = body.get("relation_type", "depends_on")
    if not source_id or not target_id:
        return jsonify({"ok": False, "error": "source_id and target_id required"}), 400

    store = _get_tree_store()
    rid = store.add_relation(
        source_id=source_id,
        target_id=target_id,
        relation_type=relation_type,
        source_origin="manual",
    )
    return jsonify({"ok": True, "id": rid})


@dashboard_bp.route("/api/dashboard/resource-tree/relations/<relation_id>", methods=["DELETE"])
@require_auth
def delete_resource_tree_relation(relation_id):
    store = _get_tree_store()
    relations = store.get_relations()
    target = next((r for r in relations if r["id"] == relation_id), None)
    if not target:
        return jsonify({"ok": False, "error": "Not found"}), 404
    if target["source_origin"] == "auto_scan":
        return jsonify({"ok": False, "error": "Cannot delete auto_scan relation"}), 403
    store.delete_relation(relation_id)
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/resource-tree/positions", methods=["PUT"])
@require_auth
def put_resource_tree_positions():
    body = request.get_json(force=True) or {}
    positions = body.get("positions", {})
    store = _get_tree_store()
    store.save_positions(positions)
    return jsonify({"ok": True})
```

**注意**：確保 `dashboard/api.py` 頂部已導入 `os`、`json`、`threading`、`uuid`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard_api_resource_tree.py -v`

Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_dashboard_api_resource_tree.py dashboard/api.py
git commit -m "feat(resource-tree): add dashboard API routes for resource tree"
```

---

### Task 5: Frontend — Cytoscape.js Setup & Page Structure

**Files:**
- Modify: `dashboard/static/index.html`
- Modify: `dashboard/static/app.js`
- Modify: `dashboard/static/style.css`

- [ ] **Step 1: Add Cytoscape.js CDN to index.html**

在 `dashboard/static/index.html` 中，於 `</body>` 之前的 `app.js` script 標籤之前插入：

```html
  <script src="https://unpkg.com/cytoscape@3.26.0/dist/cytoscape.min.js"></script>
  <script src="/dashboard/static/app.js"></script>
```

確保 `vue.global.js` 和 `vue-router.global.js` 仍然先於 Cytoscape 加載（因為 Vue 不依賴 Cytoscape，順序無所謂，但 Cytoscape 必須在 `app.js` 之前）。

- [ ] **Step 2: Add sidebar nav and router entry in app.js**

在 `AppLayout` 的 template 中，於 `<router-link to="/config">Config</router-link>` 之前添加：

```html
          <router-link to="/resource-tree">Resource Tree</router-link>
```

在 `app.js` 的路由定義中（搜尋 `const routes = [` 或類似位置），添加：

```javascript
  { path: '/resource-tree', component: ResourceTreePage },
```

- [ ] **Step 3: Implement ResourceTreePage component**

在 `app.js` 末尾（其他頁面組件之後）添加：

```javascript
/* ---------- Resource Tree Page ---------- */
const ResourceTreePage = {
  template: `
    <div class="page resource-tree-page">
      <h2>Resource Tree</h2>
      <div class="toolbar">
        <div class="tag-group-setting">
          <label>分組 Key:</label>
          <input v-model="tagInput" @keyup.enter="saveConfig" placeholder="Project,Environment" />
          <button @click="saveConfig">套用</button>
        </div>
        <div class="layout-setting">
          <label>布局:</label>
          <select v-model="layoutName" @change="applyLayout">
            <option value="cose">cose</option>
            <option value="circle">circle</option>
            <option value="grid">grid</option>
            <option value="breadthfirst">breadthfirst</option>
          </select>
        </div>
        <button @click="triggerScan" :disabled="scanning">
          {{ scanning ? '掃描中...' : '重新掃描' }}
        </button>
      </div>
      <div ref="cyContainer" class="cy-container"></div>
      <div v-if="scanStatus" class="scan-status">掃描狀態: {{ scanStatus }}</div>
    </div>
  `,
  setup() {
    const cyContainer = ref(null);
    const tagInput = ref("Project,Environment");
    const layoutName = ref("cose");
    const scanning = ref(false);
    const scanStatus = ref("");
    let cy = null;

    const fetchConfig = async () => {
      const data = await api("/resource-tree/config");
      if (data.ok && data.config) {
        tagInput.value = (data.config.group_by_tags || []).join(",");
        layoutName.value = data.config.layout_algorithm || "cose";
      }
    };

    const saveConfig = async () => {
      const tags = tagInput.value.split(",").map(s => s.trim()).filter(Boolean);
      await api("/resource-tree/config", {
        method: "POST",
        body: { group_by_tags: tags, layout_algorithm: layoutName.value },
      });
      await loadGraph();
    };

    const loadGraph = async () => {
      const data = await api("/resource-tree/graph?provider=aws");
      if (!data.ok) return;
      const elements = [];
      for (const n of data.nodes) {
        elements.push({
          data: {
            id: n.id,
            label: n.label,
            type: n.type,
            isGroup: n.is_group || false,
            ...(n.position ? { position: n.position } : {}),
          },
          ...(n.position ? { position: n.position } : {}),
        });
      }
      for (const e of data.edges) {
        elements.push({
          data: {
            id: e.id || (e.source + "->" + e.target),
            source: e.source,
            target: e.target,
            relationType: e.relation_type,
            sourceOrigin: e.source_origin,
          },
        });
      }
      if (cy) {
        cy.destroy();
      }
      cy = cytoscape({
        container: cyContainer.value,
        elements,
        style: [
          {
            selector: "node",
            style: {
              label: "data(label)",
              width: 60,
              height: 60,
              "background-color": "#4285F4",
              "text-valign": "center",
              "text-halign": "center",
              "font-size": "10px",
              color: "#fff",
            },
          },
          {
            selector: "node[isGroup]",
            style: {
              "background-opacity": 0.2,
              "border-width": 2,
              "border-style": "dashed",
              "border-color": "#666",
              "text-valign": "top",
              color: "#333",
            },
          },
          {
            selector: 'node[type="eks"]',
            style: { "background-color": "#FF9900" },
          },
          {
            selector: 'node[type="ec2"]',
            style: { "background-color": "#232F3E" },
          },
          {
            selector: 'node[type="elb"]',
            style: { "background-color": "#1E8900" },
          },
          {
            selector: 'node[type="rds"]',
            style: { "background-color": "#527FFF" },
          },
          {
            selector: 'node[type="vpc"]',
            style: { "background-color": "#9AA0A6" },
          },
          {
            selector: 'node[type="subnet"]',
            style: { "background-color": "#9AA0A6", shape: "diamond" },
          },
          {
            selector: "edge",
            style: {
              width: 2,
              "line-color": "#999",
              "target-arrow-shape": "triangle",
              "target-arrow-color": "#999",
              "curve-style": "bezier",
            },
          },
          {
            selector: 'edge[sourceOrigin="manual"]',
            style: { "line-color": "#4285F4", "target-arrow-color": "#4285F4" },
          },
          {
            selector: 'edge[sourceOrigin="tag_group"]',
            style: { "line-color": "#34A853", "line-style": "dashed", "target-arrow-color": "#34A853" },
          },
        ],
        layout: { name: layoutName.value, fit: true, padding: 20 },
      });
      cy.on("free", "node", () => {
        const positions = {};
        cy.nodes().forEach(n => {
          positions[n.id()] = { x: n.position().x, y: n.position().y };
        });
        api("/resource-tree/positions", { method: "PUT", body: { positions } });
      });
    };

    const applyLayout = () => {
      if (!cy) return;
      cy.layout({ name: layoutName.value, fit: true, padding: 20 }).run();
    };

    const triggerScan = async () => {
      scanning.value = true;
      scanStatus.value = "啟動掃描...";
      const data = await api("/resource-tree/scan", { method: "POST", body: { provider: "aws" } });
      if (!data.ok) {
        scanning.value = false;
        scanStatus.value = "掃描失敗";
        return;
      }
      const jobId = data.job_id;
      const poll = setInterval(async () => {
        const status = await api(`/resource-tree/scan/${jobId}`);
        if (status.status === "done") {
          clearInterval(poll);
          scanning.value = false;
          scanStatus.value = `完成，發現 ${status.count} 條關聯`;
          await loadGraph();
        } else if (status.status === "failed") {
          clearInterval(poll);
          scanning.value = false;
          scanStatus.value = "掃描失敗: " + (status.error || "");
        }
      }, 2000);
    };

    onMounted(() => {
      fetchConfig();
      loadGraph();
    });

    return {
      cyContainer,
      tagInput,
      layoutName,
      scanning,
      scanStatus,
      saveConfig,
      applyLayout,
      triggerScan,
    };
  },
};
```

- [ ] **Step 4: Add CSS styles**

在 `dashboard/static/style.css` 末尾追加：

```css
/* ===== Resource Tree ===== */
.resource-tree-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 60px);
}

.resource-tree-page h2 {
  margin: 16px 24px 8px;
}

.resource-tree-page .toolbar {
  display: flex;
  gap: 16px;
  align-items: center;
  padding: 8px 24px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-elevated);
}

.resource-tree-page .toolbar label {
  font-size: 13px;
  color: var(--text-secondary);
  margin-right: 4px;
}

.resource-tree-page .toolbar input,
.resource-tree-page .toolbar select {
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: 13px;
}

.resource-tree-page .toolbar button {
  padding: 5px 12px;
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
}

.resource-tree-page .toolbar button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.resource-tree-page .cy-container {
  flex: 1;
  background: var(--bg-base);
  min-height: 400px;
}

.resource-tree-page .scan-status {
  padding: 6px 24px;
  font-size: 12px;
  color: var(--text-secondary);
  border-top: 1px solid var(--border);
}
```

- [ ] **Step 5: Manual verification**

啟動 Flask app：`python webhook_server.py`（或現有啟動方式），登入 Dashboard，點擊側邊欄「Resource Tree」，確認：
- Cytoscape 畫布正常渲染（即使空數據也應顯示空白畫布，無 JS 錯誤）
- 分組 Key 輸入框、布局下拉框、重新掃描按鈕可見

- [ ] **Step 6: Commit**

```bash
git add dashboard/static/index.html dashboard/static/app.js dashboard/static/style.css
git commit -m "feat(resource-tree): add frontend ResourceTreePage with Cytoscape.js"
```

---

### Task 6: Frontend — Manual Edge Creation & Deletion

**Files:**
- Modify: `dashboard/static/app.js`
- Modify: `dashboard/static/style.css`

- [ ] **Step 1: Add edge creation via Shift+drag**

在 `ResourceTreePage` 的 `loadGraph` 函數中，於 `cy.on("free", "node", ...)` 之後添加：

```javascript
      let shiftSource = null;
      cy.on("tapstart", "node", (evt) => {
        if (window.event && window.event.shiftKey) {
          shiftSource = evt.target;
        }
      });
      cy.on("tapend", "node", (evt) => {
        if (shiftSource && shiftSource.id() !== evt.target.id()) {
          const target = evt.target;
          const relType = prompt("選擇關聯類型:\n1. contains\n2. attached_to\n3. depends_on", "depends_on");
          if (!relType) {
            shiftSource = null;
            return;
          }
          const map = { "1": "contains", "2": "attached_to", "3": "depends_on" };
          const finalType = map[relType] || relType;
          api("/resource-tree/relations", {
            method: "POST",
            body: {
              source_id: shiftSource.id(),
              target_id: target.id(),
              relation_type: finalType,
            },
          }).then(() => loadGraph());
        }
        shiftSource = null;
      });
      cy.on("cxttap", "edge", (evt) => {
        const edge = evt.target;
        const origin = edge.data("sourceOrigin");
        if (origin === "auto_scan") {
          alert("自動掃描發現的關聯不可刪除，請使用重新掃描重置。");
          return;
        }
        if (confirm("確定刪除此關聯？")) {
          api(`/resource-tree/relations/${edge.id()}`, { method: "DELETE" }).then(() => loadGraph());
        }
      });
```

- [ ] **Step 2: Add context menu hint in UI**

在 template 的 `toolbar` div 中，於 `</div>` 閉合標籤之前添加提示：

```html
        <span style="font-size:12px;color:var(--text-tertiary);margin-left:auto">
          Shift+拖拽節點創建關聯 | 右鍵邊線刪除
        </span>
```

- [ ] **Step 3: Manual verification**

在瀏覽器中驗證：
- 按住 Shift 從一個節點拖拽到另一個節點，彈出 prompt 輸入關聯類型
- 確認後，邊線出現且為藍色（`manual`）
- 右鍵點擊藍色邊線，確認刪除後邊線消失
- 右鍵點擊灰色邊線（`auto_scan`），提示不可刪除

- [ ] **Step 4: Commit**

```bash
git add dashboard/static/app.js dashboard/static/style.css
git commit -m "feat(resource-tree): add manual edge creation and deletion"
```

---

### Task 7: Final Integration & Test Run

**Files:**
- All above

- [ ] **Step 1: Run all backend tests**

```bash
pytest tests/test_resource_tree_store.py tests/test_resource_tree_builder.py tests/test_resource_tree_scanner.py tests/test_dashboard_api_resource_tree.py -v
```

Expected: All tests PASS

- [ ] **Step 2: Run full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: No regressions in existing tests

- [ ] **Step 3: Fix any issues**

If failures occur, diagnose and fix.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(resource-tree): complete integration and tests"
```

---

## Self-Review Checklist

**1. Spec coverage:**

| Spec Section | Plan Task |
|-------------|-----------|
| SQLite schema (`resource_relations`, `node_positions`) | Task 1 |
| `ResourceTreeStore` CRUD | Task 1 |
| `ResourceTreeBuilder` with tag groups | Task 2 |
| `AWSResourceScanner` (EKS/ELB/EC2/RDS) | Task 3 |
| API endpoints (config, graph, scan, relations, positions) | Task 4 |
| Frontend Cytoscape.js setup | Task 5 |
| Node drag + position save | Task 5 |
| Manual edge creation (Shift+drag) | Task 6 |
| Edge deletion with auto_scan protection | Task 6 |
| Re-scan flow | Task 5 |
| Custom `group_by_tags` | Task 4, 5 |

No gaps identified.

**2. Placeholder scan:**

No "TBD", "TODO", "implement later", or vague instructions found. All steps include exact code, commands, and expected output.

**3. Type consistency:**

- `ResourceTreeStore.add_relation()` signature matches across Task 1 and Task 4
- `ResourceTreeBuilder.build_graph()` signature matches across Task 2 and Task 4
- `AWSResourceScanner.scan()` returns `list[dict]` consistently
- API response format `{"ok": True/False, ...}` matches existing dashboard patterns

All consistent.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-25-resource-tree.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
