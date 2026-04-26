# Dashboard Resources 历史指标数据设计文档

## 背景

当前 kiro-devops 的 Dashboard Resources 画面实时通过 boto3 从 AWS API 获取资源与 CloudWatch 指标，仅有 5 分钟内存缓存，无持久化存储。用户希望：

1. 将 CloudWatch 指标数据持久化到本地存储
2. 定时同步资源过去 24 小时历史数据（首次回溯 30 天）
3. 在 Resources 画面呈现历史趋势，使用者可依据 CPU 用量推断资源活动情况

## 目标

- 支持 300~500 台 EC2 规模，存储半年历史数据
- 首次运行时记录过去 30 天的每小时数据
- 之后每日运行一次，累积记录前一天 24 小时数据
- 不记录资源状态变更，仅记录 CloudWatch 指标
- 先实现 CPUUtilization，预留多指标扩展结构

## 非目标

- 不追踪 EC2/RDS 资源状态（running/stopped）的变更历史
- 不引入外部时序数据库（InfluxDB/TimescaleDB）
- 不修改现有资源列表 API 的返回结构

## 架构设计

### 数据模型

#### 原始小时级数据表（`hourly_metrics`）

存储在按月分库的 `raw_metrics_YYYY_MM.db` 中：

```sql
CREATE TABLE hourly_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    value REAL NOT NULL,
    region TEXT,
    created_at INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(resource_id, metric_name, timestamp)
);

CREATE INDEX idx_hourly_lookup ON hourly_metrics(resource_id, metric_name, timestamp);
```

#### 日级聚合数据表（`daily_aggregated`）

存储在统一的 `aggregated_metrics.db` 中：

```sql
CREATE TABLE daily_aggregated (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    date TEXT NOT NULL,
    min_value REAL NOT NULL,
    avg_value REAL NOT NULL,
    p95_value REAL NOT NULL,
    max_value REAL NOT NULL,
    region TEXT,
    UNIQUE(resource_id, metric_name, date)
);

CREATE INDEX idx_daily_lookup ON daily_aggregated(resource_id, metric_name, date);
```

### 存储架构

按类型分离：

```
memory_db/
├── raw_metrics_2026_04.db      ← 原始 hourly，按月分库，永久保留
├── raw_metrics_2026_03.db
├── raw_metrics_2026_02.db
│   ...
└── aggregated_metrics.db       ← 日级聚合，统一存储
      └── daily_aggregated
```

- **原始库**：严格按月分库，只存小时级数据。使用者自行备份/管理，系统不自动删除。
- **聚合库**：单个数据库文件，存所有资源的日级聚合。保留 180 天，超期自动清理。

### 降采样策略

| 层级 | 精度 | 保留期 | 清理方式 |
|------|------|--------|---------|
| 热数据 | 小时级原始点 | 永久（原始库） | 使用者自行管理 |
| 温数据 | 日级聚合（min/avg/p95/max） | 180 天 | 按日期行删除 |

降采样触发时机：每日增量同步完成后，检查"完整结束的上一个月"是否已降采样。若未降采样，将该月 hourly 数据按 `resource_id + metric_name + date` 分组聚合，写入 `aggregated_metrics.db`。

## 组件设计

### 新增模块

| 文件 | 职责 |
|------|------|
| `dashboard/metrics_store.py` | 原始库与聚合库的连接管理、读写查询、降采样执行 |
| `scripts/sync_resource_metrics.py` | 独立同步脚本，支持 `--backfill`、`--incremental`、`--downsample` |

### `dashboard/metrics_store.py`

核心接口：

- `get_raw_db(year, month)`：返回对应月份原始库的连接（懒加载、连接复用）
- `get_aggregated_db()`：返回聚合库连接
- `write_hourly(records)`：批量写入小时数据（UPSERT 幂等）
- `downsample_month(year, month)`：将指定月份的 hourly 聚合为 daily
- `query_history(resource_id, metric, range)`：自动路由到 hourly 或 daily 表
  - `24h`/`7d`/`30d`：读最近 1~2 个 `raw_metrics_*.db` 的 hourly 表
  - `180d`：读 `aggregated_metrics.db` 的 daily 表

### `scripts/sync_resource_metrics.py`

执行流程（增量模式）：

1. 读取 `dashboard_config.json` 中的 `regions` 列表
2. 并发调用 `discover_ec2()` + `discover_rds()`（复用 `dashboard/resources.py` 逻辑）
3. 对每台资源查询 CloudWatch `CPUUtilization`，`Period=3600s`，取前一天 24 个数据点
4. 批量写入 `raw_metrics_当前月.db`（SQLite UPSERT，跳过已存在的 timestamp）
5. 检查是否需要降采样：若上月 hourly 已完整且未降采样，聚合为 daily 并写入 `aggregated_metrics.db`

命令行接口：

```bash
# 首次运行：回溯过去30天
python3 scripts/sync_resource_metrics.py --backfill

# 每日增量：同步前24小时
python3 scripts/sync_resource_metrics.py --incremental

# 手动降采样指定月份
python3 scripts/sync_resource_metrics.py --downsample 2026 03
```

### API 设计

#### 新增路由

```
GET /api/dashboard/resources/{resource_id}/history?metric=cpu_utilization&range=24h|7d|30d|180d
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `resource_id` | path | 是 | 如 `ec2:cn-north-1:i-123` |
| `metric` | query | 否 | 默认 `cpu_utilization` |
| `range` | query | 否 | 默认 `24h`，可选 `7d`、`30d`、`180d` |

响应结构：

```json
{
  "resource_id": "ec2:cn-north-1:i-123",
  "metric": "cpu_utilization",
  "range": "24h",
  "granularity": "hourly",
  "data": [
    {"timestamp": 1714113600, "value": 12.5},
    {"timestamp": 1714117200, "value": 15.2}
  ],
  "stats": {
    "min": 5.1,
    "avg": 12.3,
    "p95": 28.7,
    "max": 45.2
  }
}
```

#### 现有路由保持不变

`GET /api/dashboard/resources` 的返回结构不变，历史数据通过新路由按需查询，避免列表接口变重。

### 前端设计

#### ResourcesPage 增强

现有资源列表保持不变。点击某行资源（或新增趋势图标）展开 **历史趋势面板**：

- **时间范围切换**：`[24h] [7d] [30d] [180d]`
- **趋势折线图**：复用现有 SVG `<polyline>` 绘制逻辑，扩展支持 X 轴时间标签
- **统计值**：`MIN / AVG / P95 / MAX`
- **粒度提示**：`24h/7d/30d` 为 hourly，`180d` 为 daily

#### 交互细节

- **懒加载**：面板展开时才调用历史 API
- **前端缓存**：内存缓存已查询过的 range，切换资源时不重复请求

## 数据流

```
dashboard_config.json
  ├── regions ─────────────────┐
  │                            │
  ▼                            │
┌─────────────────────────┐    │
│ sync_resource_metrics.py│    │
│   (cron 每日触发)        │    │
└─────────────────────────┘    │
         │                     │
         ▼                     │
┌─────────────────────────┐    │
│ AWS CloudWatch API      │    │
│ CPUUtilization (1h)     │    │
└─────────────────────────┘    │
         │                     │
         ▼                     │
┌─────────────────────────┐    │
│ raw_metrics_YYYY_MM.db  │    │
│   hourly_metrics        │    │
└─────────────────────────┘    │
         │                     │
         ▼                     │
┌─────────────────────────┐    │
│ downsample (月度触发)    │    │
└─────────────────────────┘    │
         │                     │
         ▼                     │
┌─────────────────────────┐    │
│ aggregated_metrics.db   │◄───┘
│   daily_aggregated      │
└─────────────────────────┘
         ▲
         │ GET /resources/{id}/history
         │
┌─────────────────────────┐
│  dashboard/api.py       │
└─────────────────────────┘
         ▲
         │
┌─────────────────────────┐
│  app.js ResourcesPage   │
│  (展开面板 + SVG 图表)   │
└─────────────────────────┘
```

## 错误处理

| 场景 | 策略 |
|------|------|
| CloudWatch API 限流/失败 | 指数退避重试 3 次；失败记录到日志，不中断其他资源同步 |
| SQLite 写入冲突 | 脚本独立进程运行，无并发写入；UPSERT 保证幂等 |
| 磁盘空间不足 | 脚本启动前检查目录空间，低于 1GB 打印警告但不中断 |
| 首次回溯 30 天超时 | 支持断点续传：已同步的 timestamp 跳过，中断后可重新运行 `--backfill` |
| 降采样失败 | 记录未降采样的月份，下次同步时重试 |

## 定时调度

Cron 配置示例：

```cron
# 每日凌晨 3 点同步前24小时数据
0 3 * * * cd /home/ubuntu/kiro-devops && /usr/bin/python3 scripts/sync_resource_metrics.py --incremental >> /var/log/kiro-metrics-sync.log 2>&1
```

首次运行需手动执行：

```bash
python3 scripts/sync_resource_metrics.py --backfill
```

## 测试策略

| 测试类型 | 内容 |
|---------|------|
| 单元测试 | `dashboard/metrics_store.py` 的读写、跨月查询路由、降采样统计计算 |
| 集成测试 | `scripts/sync_resource_metrics.py` 用 mock CloudWatch 响应验证完整同步流程 |
| API 测试 | `GET /resources/{id}/history` 各 range 路由返回正确粒度与统计值 |

## 扩展性预留

- `metric_name` 字段支持后续扩展内存、磁盘、网络等指标
- `raw_metrics_*.db` 按资源类型分表可在未来通过 `metric_name` 过滤实现
- 聚合库 `daily_aggregated` 增加新指标的列或新表无需修改原始库结构

## 数据量估算

按 500 台 EC2、单指标（CPU）、半年场景：

| 数据类型 | 计算方式 | 总量 |
|---------|---------|------|
| 原始 hourly | 500 × 24 × 180 天 | 216 万条 |
| 日级聚合 | 500 × 180 天 | 9 万条 |

即使扩展到 1000 资源、4 指标，原始库在 SQLite 可控范围内（按月分库后单库约 72 万条）。
