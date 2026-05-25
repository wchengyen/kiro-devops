# Resource Tree 設計文件

**日期**: 2026-05-25  
**作者**: Kimi Code CLI  
**狀態**: 待審查  

---

## 1. 背景與目標

### 1.1 現狀

kiro-devops Dashboard 目前已有資源列表頁面（`/resources/:provider`），支持 AWS 與 Tencent 雙雲資源展示、成本評分、Tag 過濾。但：

- **沒有資源關聯模型**：資源之間的依賴關係（如 EKS 與 EC2、ELB 與 Target Group）完全缺失
- **沒有可視化架構圖**：無法直觀呈現雲端架構拓撲
- **Tags 僅用於過濾**：未用於構建分組或層級關係

### 1.2 目標

在 Dashboard 中新增 **Resource Tree** 頁面，達成以下三個場景：

1. **故障排查**：收到警報時快速查看資源依賴關係與影響範圍
2. **成本歸屬**：按自定義 Tag Key（如 `Project`、`Team`）分組聚合資源
3. **架構盤點**：視覺化呈現雲端架構，支持交互編輯與手動調整

### 1.3 設計原則

- **最小侵入**：复用現有 Provider 抽象、Vue 3 Global Build 前端、SQLite 存儲
- **用戶可控**：自動掃描為輔，手動編輯為主；無自動排程刷新，用戶手動觸發「重新掃描」
- **預留擴展**：所有結構化數據存 SQLite，支持未來多用戶、多布局快照

---

## 2. 數據模型與存儲

### 2.1 新增 SQLite 表

#### `resource_relations`

存儲資源之間的所有關聯（自動掃描發現 + Tags 分組生成 + 用戶手動創建）。

```sql
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
```

| 字段 | 說明 |
|------|------|
| `id` | UUID，主鍵 |
| `source_id` | 來源資源 `unique_id`（如 `aws:ec2:cn-north-1:i-123`）或虛擬群組 ID（如 `group:Project:myapp`） |
| `target_id` | 目標資源 `unique_id` 或虛擬群組 ID |
| `relation_type` | `contains`（包含）、`attached_to`（附加）、`belongs_to`（屬於）、`grouped_by`（分組） |
| `source_origin` | `auto_scan`（自動掃描）、`manual`（手動創建）、`tag_group`（Tag 分組生成） |
| `provider` | `aws`、`tencent`，方便按雲過濾 |
| `created_at` / `updated_at` | ISO 8601 時間戳 |

#### `node_positions`

存儲拓撲圖節點的用戶自定義位置，預留擴展性。

```sql
CREATE TABLE IF NOT EXISTS node_positions (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL UNIQUE,
    layout_name TEXT NOT NULL DEFAULT 'default',
    x REAL NOT NULL,
    y REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_node_positions_layout ON node_positions(layout_name);
```

### 2.2 Dashboard Config 擴展

`dashboard_config.json` 中新增 `resource_tree` 區塊，存儲用戶偏好配置：

```json
{
  "resource_tree": {
    "group_by_tags": ["Project", "Environment"],
    "layout_algorithm": "cose",
    "default_provider": "aws"
  }
}
```

**注意**：節點位置**不**存在這裡，而是存在 SQLite `node_positions` 表中。

---

## 3. 後端架構與 API

### 3.1 新增模組

```
dashboard/
├── resource_tree.py       # 核心邏輯：Store、Builder、Scanner
├── resource_tree_store.py # （可選拆分）SQLite 操作
└── api.py                 # 現有：新增 Resource Tree 路由
```

#### `ResourceTreeStore`

操作 `resource_relations` 與 `node_positions` 表的 CRUD。

```python
class ResourceTreeStore:
    def __init__(self, db_path: str = "memory_db/resource_tree.db"): ...
    
    def get_relations(self, provider: str | None = None) -> list[dict]: ...
    def add_relation(self, source_id, target_id, relation_type, source_origin, provider) -> str: ...
    def delete_relation(self, relation_id: str) -> bool: ...
    def clear_auto_scan_relations(self, provider: str) -> int: ...
    
    def get_positions(self, layout_name: str = "default") -> dict[str, dict]: ...
    def save_positions(self, positions: dict[str, dict], layout_name: str = "default") -> None: ...
```

#### `ResourceTreeBuilder`

將資源列表、關聯數據、Tag 分組配置合併為拓撲圖所需的 `nodes` + `edges`。

```python
class ResourceTreeBuilder:
    def build_graph(
        self,
        resources: list[Resource],
        relations: list[dict],
        group_by_tags: list[str],
        positions: dict[str, dict]
    ) -> dict:
        """
        Returns:
        {
            "nodes": [
                {
                    "id": "aws:ec2:cn-north-1:i-123",
                    "label": "web-01",
                    "type": "ec2",
                    "parent": "group:Project:myapp",  # 若為 compound node 子節點
                    "position": {"x": 100, "y": 200},
                    "data": { ... }  # 資源詳情
                },
                {
                    "id": "group:Project:myapp",
                    "label": "Project: myapp",
                    "type": "tag_group",
                    "is_group": True
                }
            ],
            "edges": [
                {
                    "id": "...",
                    "source": "aws:eks:cn-north-1:cluster-1",
                    "target": "aws:ec2:cn-north-1:i-123",
                    "relation_type": "contains",
                    "source_origin": "auto_scan"
                }
            ]
        }
        """
```

#### `AWSResourceScanner`

复用 `AWSResourceProvider` 的 boto3 session 與 region 配置，執行 AWS API 查詢。

```python
class AWSResourceScanner:
    def __init__(self, provider: AWSResourceProvider): ...
    
    def scan(self, regions: list[str]) -> list[dict]:
        """
        遍歷 regions，調用 AWS API，返回原始關聯列表：
        [
            {
                "source_id": "aws:eks:cn-north-1:cluster-1",
                "target_id": "aws:ec2:cn-north-1:i-123",
                "relation_type": "contains"
            },
            ...
        ]
        """
```

### 3.2 API 端點

在 `dashboard/api.py` 中，於現有 Blueprint 下新增以下路由：

| 端點 | 方法 | 請求體 | 響應 | 說明 |
|------|------|--------|------|------|
| `/api/dashboard/resource-tree/config` | GET | - | `{ "group_by_tags": [...], "layout_algorithm": "cose", ... }` | 讀取 Resource Tree 配置 |
| `/api/dashboard/resource-tree/config` | POST | `{ "group_by_tags": [...], "layout_algorithm": "cose" }` | `{ "ok": true }` | 更新配置 |
| `/api/dashboard/resource-tree/graph` | GET | Query: `?provider=aws` | `{ "ok": true, "nodes": [...], "edges": [...] }` | 獲取完整拓撲圖數據 |
| `/api/dashboard/resource-tree/scan` | POST | `{ "provider": "aws" }` | `{ "ok": true, "job_id": "..." }` | 觸發自動掃描（異步執行） |
| `/api/dashboard/resource-tree/scan/<job_id>` | GET | - | `{ "ok": true, "status": "running\|done\|failed", "count": 42 }` | 查詢掃描任務狀態 |
| `/api/dashboard/resource-tree/relations` | POST | `{ "source_id", "target_id", "relation_type" }` | `{ "ok": true, "id": "..." }` | 手動創建關聯（`source_origin = manual`） |
| `/api/dashboard/resource-tree/relations/<id>` | DELETE | - | `{ "ok": true }` | 刪除關聯（僅允許刪除 `manual` 或 `tag_group`，`auto_scan` 不可刪除） |
| `/api/dashboard/resource-tree/positions` | PUT | `{ "positions": { "node_id": {"x": 100, "y": 200}, ... } }` | `{ "ok": true }` | 批量保存節點位置 |

### 3.3 認證與權限

所有端點使用現有 `@require_auth` 裝飾器，與 Dashboard 其他 API 一致。

### 3.4 錯誤處理

- 掃描過程中單個 region/API 失敗不影響整體，記錄 warning 並繼續
- 掃描完成後返回成功數與失敗數
- 手動刪除 `auto_scan` 關聯時返回 `403 Forbidden`

---

## 4. 自動掃描邏輯

### 4.1 掃描觸發方式

- **無自動排程**：不依賴 cron 或定時任務
- **手動觸發**：用戶在 Resource Tree 頁面點擊「重新掃描」按鈕
- **異步執行**：後端啟動 background thread 執行掃描，前端通過 `job_id` 輪詢狀態

### 4.2 掃描流程

```
POST /scan
  → 生成 job_id
  → 啟動 background thread
  → 返回 { ok: true, job_id }

Background Thread:
  1. ResourceTreeStore.clear_auto_scan_relations("aws")
  2. 遍歷 dashboard_config.json 中 aws.regions
  3. 執行以下掃描子任務（每個子任務失敗不影響其他）
  4. 寫入 resource_relations（source_origin = auto_scan）
  5. 更新 job 狀態為 done
```

### 4.3 AWS 掃描子任務

#### 4.3.1 EKS → EC2

```python
def _scan_eks_ec2(self, region: str, session) -> list[dict]:
    eks = session.client("eks", region_name=region)
    ec2 = session.client("ec2", region_name=region)
    asg = session.client("autoscaling", region_name=region)
    
    relations = []
    clusters = eks.list_clusters()["clusters"]
    for cluster_name in clusters:
        nodegroups = eks.list_nodegroups(clusterName=cluster_name)["nodegroups"]
        for ng_name in nodegroups:
            ng = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=ng_name)["nodegroup"]
            for asg_name in ng.get("resources", {}).get("autoScalingGroups", []):
                asg_detail = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name["name"]])["AutoScalingGroups"][0]
                for instance in asg_detail.get("Instances", []):
                    relations.append({
                        "source_id": f"aws:eks:{region}:{cluster_name}",
                        "target_id": f"aws:ec2:{region}:{instance['InstanceId']}",
                        "relation_type": "contains"
                    })
    return relations
```

#### 4.3.2 ELB/ALB → EC2/EKS

```python
def _scan_elb_targets(self, region: str, session) -> list[dict]:
    elbv2 = session.client("elbv2", region_name=region)
    
    relations = []
    lbs = elbv2.describe_load_balancers()["LoadBalancers"]
    for lb in lbs:
        lb_id = lb["LoadBalancerArn"].split("/")[-1]
        tgs = elbv2.describe_target_groups(LoadBalancerArn=lb["LoadBalancerArn"])["TargetGroups"]
        for tg in tgs:
            health = elbv2.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])["TargetHealthDescriptions"]
            for target in health:
                target_id = target["Target"]["Id"]
                # target_id 可能是 EC2 instance ID 或 IP
                if target_id.startswith("i-"):
                    relations.append({
                        "source_id": f"aws:elb:{region}:{lb_id}",
                        "target_id": f"aws:ec2:{region}:{target_id}",
                        "relation_type": "attached_to"
                    })
    return relations
```

#### 4.3.3 EC2 → Subnet → VPC

```python
def _scan_ec2_network(self, region: str, session) -> list[dict]:
    ec2 = session.client("ec2", region_name=region)
    
    relations = []
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
                    "relation_type": "belongs_to"
                })
            if vpc_id:
                relations.append({
                    "source_id": f"aws:subnet:{region}:{subnet_id}",
                    "target_id": f"aws:vpc:{region}:{vpc_id}",
                    "relation_type": "belongs_to"
                })
    return relations
```

#### 4.3.4 RDS → Subnet → VPC

```python
def _scan_rds_network(self, region: str, session) -> list[dict]:
    rds = session.client("rds", region_name=region)
    
    relations = []
    dbs = rds.describe_db_instances()["DBInstances"]
    for db in dbs:
        db_id = db["DBInstanceIdentifier"]
        subnet_group = db.get("DBSubnetGroup", {})
        subnet_group_name = subnet_group.get("DBSubnetGroupName")
        vpc_id = subnet_group.get("VpcId")
        
        if subnet_group_name:
            for subnet in subnet_group.get("Subnets", []):
                subnet_id = subnet["SubnetIdentifier"]
                relations.append({
                    "source_id": f"aws:rds:{region}:{db_id}",
                    "target_id": f"aws:subnet:{region}:{subnet_id}",
                    "relation_type": "belongs_to"
                })
        if vpc_id:
            relations.append({
                "source_id": f"aws:rds:{region}:{db_id}",
                "target_id": f"aws:vpc:{region}:{vpc_id}",
                "relation_type": "belongs_to"
            })
    return relations
```

---

## 5. 前端拓撲圖設計

### 5.1 技術選型

- **函式庫**：Cytoscape.js 3.26.0（CDN 引入）
- **布局擴展**：cytoscape-cose-bilkent（更好的 Compound Graph 布局）
- **原因**：
  - 與現有 Vue 3 Global Build 完全兼容（無需構建工具）
  - 原生支持節點拖拽、邊線操作、Compound Nodes（群組嵌套）
  - 布局算法豐富（cose、circle、grid、breadthfirst）

### 5.2 新增頁面與路由

```javascript
// dashboard/static/app.js 中新增
const ResourceTreePage = {
  template: `...`,
  data() { ... },
  mounted() { this.initCytoscape(); }
};

// Vue Router 新增路由
{ path: '/resource-tree', component: ResourceTreePage }
```

側邊欄新增「Resource Tree」導航項。

### 5.3 頁面布局

```
+-------------------------------------------------------------+
|  [側邊欄]  |  Resource Tree                                |
|            |  +-------------------------------------------+|
|            |  |  [分組設定 ▼] [布局 ▼] [重新掃描] [圖例]  ||
|            |  +-------------------------------------------+|
|            |  |                                           ||
|            |  |                                           ||
|            |  |         Cytoscape 拓撲圖畫布               ||
|            |  |                                           ||
|            |  |                                           ||
|            |  +-------------------------------------------+|
+-------------------------------------------------------------+
|  底部狀態欄：節點數: 42 | 邊線數: 38 | 掃描狀態: 就緒      |
+-------------------------------------------------------------+
```

**左側面板控制項**：
- **分組 Key 設定**：多選下拉框，選擇 Tag Key（如 `Project`、`Environment`），即時重新渲染拓撲圖
- **布局算法**：`cose`（預設）、`circle`、`grid`、`breadthfirst`，切換即時生效
- **重新掃描**：觸發 POST `/scan`，顯示進度條，完成後自動刷新圖
- **圖例**：各資源類型對應的顏色與圖標說明

### 5.4 節點與邊線樣式

| 資源類型 | 形狀 | 顏色 | 圖標 |
|----------|------|------|------|
| EKS | 圓角矩形 | `#FF9900` | ⎈ |
| EC2 | 矩形 | `#232F3E` | 🖥 |
| ELB/ALB | 六邊形 | `#1E8900` | ⚖ |
| RDS | 圓柱 | `#527FFF` | 🗄 |
| VPC | 雲形 | `#9AA0A6` | ☁ |
| Subnet | 菱形 | `#9AA0A6` | 🔷 |
| Tag Group | 虛線框（Compound Node） | `#E8EAED` | 📁 |

**邊線樣式**：
- `auto_scan`：實線，灰色 `#999`
- `manual`：實線，藍色 `#4285F4`
- `tag_group`：虛線，綠色 `#34A853`

### 5.5 點擊與詳情面板

- **單擊節點**：右側滑出抽屜（Drawer），顯示資源詳情（复用現有資源卡片樣式：名稱、狀態、Tags、成本評分、CPU 趨勢 Sparkline）
- **雙擊群組節點**：展開/折疊群組內子節點
- **框選多節點**：批量拖拽移動

---

## 6. 交互編輯設計

### 6.1 拖拽節點

- Cytoscape `grab` / `free` 事件監聽
- 節點釋放後，延遲 500ms 批量 PUT `/api/dashboard/resource-tree/positions`
- 頁面刷新後從 `node_positions` 表恢復位置

### 6.2 手動創建關聯

- **操作**：按住 **Shift** + 從節點 A 拖拽到節點 B
- **彈出選單**：選擇 `relation_type`（`contains` / `attached_to` / `depends_on`）
- **驗證**：禁止自環（A → A）、禁止重複邊線
- **保存**：POST `/api/dashboard/resource-tree/relations`，`source_origin = manual`

### 6.3 刪除關聯

- **操作**：右鍵點擊邊線 → 彈出選單「刪除關聯」
- **限制**：僅允許刪除 `source_origin = manual` 或 `tag_group` 的邊線
- `auto_scan` 邊線禁用刪除，提示「請使用重新掃描重置自動發現的關聯」

### 6.4 重新掃描

- **操作**：點擊「重新掃描」按鈕
- **確認彈窗**：「重新掃描將清除所有自動發現的關聯並重新查詢 AWS API，手動創建的關聯不會受影響。確定繼續？」
- **執行**：POST `/scan` → 後端異步執行
- **進度**：前端輪詢 GET `/scan/<job_id>`，顯示進度條（已完成 region 數 / 總數）
- **完成**：自動 GET `/graph` 刷新拓撲圖

---

## 7. Tags 自動分組邏輯

### 7.1 分組規則

用戶在配置中指定 `group_by_tags: ["Project", "Environment"]`，後端構建拓撲圖時：

1. 遍歷所有資源，讀取每個資源的 `tags`
2. 對於每個 `group_by_tags` 中的 key，若資源存在該 tag，則生成虛擬群組節點
3. 群組節點 ID 格式：`group:<tag_key>:<tag_value>`（如 `group:Project:myapp`）
4. 生成 `resource → group` 的 `grouped_by` 關聯（`source_origin = tag_group`）
5. **多層級支持**：若 `group_by_tags = ["Project", "Environment"]`，則先按 Project 分大組，再按 Environment 分子組，形成嵌套 Compound Node

### 7.2 示例

資源：
- EC2-A: tags `{ Project: "myapp", Environment: "prod" }`
- EC2-B: tags `{ Project: "myapp", Environment: "dev" }`
- RDS-C: tags `{ Project: "api", Environment: "prod" }`

生成的群組結構：
```
group:Project:myapp
├── group:Environment:prod
│   └── EC2-A
group:Project:api
├── group:Environment:prod
│   └── RDS-C
```

**注意**：EC2-B 因為 Environment=dev，獨立在一個子組中。未被任何群組包含的資源直接顯示在根層級。

---

## 8. 測試策略

### 8.1 後端測試

| 測試項 | 說明 |
|--------|------|
| `test_resource_tree_store.py` | ResourceTreeStore 的 CRUD、索引查詢、位置存取 |
| `test_resource_tree_builder.py` | 輸入資源 + 關聯 + group_by_tags，驗證輸出 nodes/edges 結構 |
| `test_resource_tree_scanner.py` | Mock boto3 client，驗證 EKS/ELB/EC2/RDS 掃描邏輯 |
| `test_dashboard_api_resource_tree.py` | Flask test client 測試所有 API 端點（GET/POST/DELETE/PUT） |

### 8.2 前端測試

- 由於前端為無構建工具的 Vue 3 Global Build，以**手動端到端測試**為主：
  - 驗證 Cytoscape 畫布正常渲染
  - 驗證節點拖拽後位置保存與恢復
  - 驗證 Shift+拖拽創建關聯
  - 驗證重新掃描流程（進度條、完成刷新）

### 8.3 集成測試

- 使用 Moto（AWS Mock 函式庫）模擬 boto3 調用，驗證端到端掃描流程
- 驗證 Tags 分組與自動掃描關聯的合併邏輯

---

## 9. 風險與應對

| 風險 | 影響 | 應對措施 |
|------|------|----------|
| AWS API 調用限流 | 掃描失敗或超時 | 單個 API 失敗不影響整體；增加重試邏輯（exponential backoff）；未來可加緩存 |
| Cytoscape.js 節點過多（>500）性能下降 | 頁面卡頓 | 第一版目標 <200 節點；未來可引入懶加載或分層渲染 |
| 手動編輯關聯與自動掃描結果衝突 | 用戶困惑 | 邊線顏色區分來源；`auto_scan` 不可刪除；重新掃描時提示「手動關聯不受影響」 |
| 騰訊雲資源暫無自動掃描 | 騰訊用戶體驗不一致 | Tags 分組對所有 Provider 通用；騰訊自動掃描列為後續迭代 |

---

## 10. 後續迭代方向

1. **騰訊雲自動掃描**：TKE → CVM、CLB → CVM 等對應關係
2. **多布局快照**：支持保存多個布局（開發視圖、生產視圖），快速切換
3. **事件關聯高亮**：收到警報時，在拓撲圖中高亮相關資源節點與影響路徑
4. **成本氣泡圖**：節點大小映射資源成本，直觀展示高成本組件
5. **導出功能**：將拓撲圖導出為 PNG/SVG 或嵌入文檔

---

## 11. 相關文件

- `dashboard/resource_tree.py`
- `dashboard/api.py`（新增路由）
- `dashboard/static/app.js`（新增 ResourceTreePage）
- `dashboard/static/style.css`（新增拓撲圖樣式）
- `tests/test_resource_tree_*.py`

---

*本設計文件經 Brainstorming 流程確認，待實作規劃（writing-plans）進一步細化。*
