# 飞书 / 微信 ↔ Kiro Bot 桥接服务

<p align="center">
  <img src="kiro2.jpg" alt="Kiro Bot" width="180">
</p>

[![DeepWiki](https://img.shields.io/badge/DeepWiki-AI%20文档-blue)](https://deepwiki.com/wchengyen/kiro-devops)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[English Version](README_EN.md) | 中文版

在**飞书（Lark）**或**微信（iLink Bot）**中发消息，自动调用 [Kiro CLI](https://kiro.dev) 处理并回复结果。

**无需公网 IP、无需端口开放、无需 nginx 反向代理。**

> 💡 **单实例同时运行双平台**：通过 `PlatformAdapter` 抽象层，一套业务代码同时服务飞书和微信用户。

---

## 📖 AI 生成的交互式文档

👉 **[https://deepwiki.com/wchengyen/kiro-devops](https://deepwiki.com/wchengyen/kiro-devops)**

由 [DeepWiki](https://deepwiki.com) 自动生成的交互式 Wiki，包含架构图、代码分析、数据流可视化和智能问答。

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🔌 **WebSocket 长连接** | 出站连接，天然穿透 NAT/防火墙，零网络配置 |
| 🧠 **双层记忆架构** | Semantic Memory（用户偏好）+ Episodic Memory（系统事件），基于 SQLite |
| ⚡ **混合执行引擎** | 同步 120s → 超时自动转后台异步，带进度心跳 |
| 🗂️ **会话路由** | 30min 自动 resume，支持 `/new` `/resume` `/sessions` |
| 📎 **自动资源上传** | 检测 Kiro 输出中的图片/文件路径，自动上传飞书 |
| ⏰ **定时任务** | 自然语言配置周期性任务，`/schedule` 命令管理 |
| 📝 **事件录入** | `/event` 手动录入 + Webhook 外部系统推送 |
| 🚨 **EC2 告警分析** | Prometheus/CloudWatch 告警自动触发 Kiro Skill 分析并推送飞书/微信 |
| 🖥️ **Web Dashboard** | Vue 3 SPA 管理 Agents、Skills、Events、Scheduler、Config，令牌认证 |

---

## 🏗️ 架构概览

```
  飞书用户 @Bot              微信用户私聊 Bot
       ↓                          ↓
飞书云 ←WebSocket→         iLink ←长轮询→
       ↓                          ↓
┌───────────────────────────────────────────────────────────────┐
│                    gateway.py 统一入口                         │
│  ┌─────────────┐    ┌─────────────┐    ┌───────────────────┐  │
│  │FeishuAdapter│    │WeixinAdapter│    │ Webhook HTTP 服务 │  │
│  │  WebSocket  │    │  长轮询     │    │  :8080/event      │  │
│  └──────┬──────┘    └──────┬──────┘    └─────────┬─────────┘  │
│         │                  │                     │            │
│         └──────────────────┼─────────────────────┘            │
│                            ↓                                   │
│              ┌─────────────────────────┐                       │
│              │    PlatformDispatcher   │  platform:raw_id 路由 │
│              └─────────────────────────┘                       │
│                            ↓                                   │
│              ┌─────────────────────────┐                       │
│              │      MessageHandler     │                       │
│              │  平台无关业务核心        │                       │
│              │  /schedule /memory /new │                       │
│              └─────────────────────────┘                       │
└───────────────────────────────────────────────────────────────┘
                            ↓
        ┌───────────────────┼───────────────────┐
        ↓                   ↓                   ↓
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│ SemanticStore │  │ EventStore    │  │ SessionRouter │
│ SQLite FTS5   │  │ SQLite + FTS5 │  │ 会话超时管理   │
└───────────────┘  └───────────────┘  └───────────────┘
        ↓                   ↓                   ↓
        └───────────────────┼───────────────────┘
                            ↓
                      ┌─────────────┐
                      │PromptBuilder│
                      └─────────────┘
                            ↓
                      ┌─────────────┐
                      │KiroExecutor │ subprocess kiro-cli
                      └─────────────┘
                            ↓
              ┌─────────────┴─────────────┐
              ↓                           ↓
        飞书用户收到回复              微信用户收到回复
        📎 本次分析关联了 2 条历史事件
```

### 平台适配器架构

从单一飞书架构演进为多平台架构的核心是 **`PlatformAdapter` 抽象层**。

```
┌─────────────────────────────────────────────────────────────┐
│                    PlatformAdapter (ABC)                     │
├─────────────────────────────────────────────────────────────┤
│  platform: str          # 平台标识: "feishu" / "weixin"      │
│  start()                # 启动消息接收循环                   │
│  send_text(raw_id, text, context_token)  # 发送文本消息      │
├─────────────────────────────────────────────────────────────┤
│                    IncomingMessage                           │
│  { user_id, text, platform, raw_message }                   │
├─────────────────────────────────────────────────────────────┤
│                    OutgoingPayload                           │
│  { text, files[], images[] }                                │
└─────────────────────────────────────────────────────────────┘
                              ↑
         ┌────────────────────┼────────────────────┐
         │                    │                    │
   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
   │FeishuAdapter│     │WeixinAdapter│     │  (未来扩展)  │
   │lark-oapi WS │     │iLink HTTP   │     │ 钉钉/Slack  │
   └─────────────┘     └─────────────┘     └─────────────┘
```

**设计决策：**

| 设计点 | 说明 |
|--------|------|
| **统一用户 ID** | `platform:raw_id`（如 `feishu:ou_xxx`、`weixin:wxid_xxx@im.wechat`），无需跨平台绑定 |
| **平台无关业务核心** | `MessageHandler` 处理所有 `/` 命令，完全不知道消息来自哪个平台 |
| **统一发送路由** | `PlatformDispatcher` 按 `platform:raw_id` 前缀分发到对应适配器 |
| **上下文隔离** | 微信的 `context_token` 由适配器内部维护，业务层无感知 |
| **渐进式扩展** | 新增平台只需实现 `PlatformAdapter` 三个方法，零侵入业务代码 |

### 记忆架构详解

```
┌─────────────────────────────────────────────────────────────┐
│                        Memory Layer                          │
├─────────────────────────────┬───────────────────────────────┤
│     Semantic Memory         │      Episodic Memory          │
│   (语义记忆 / 用户偏好)      │    (情景记忆 / 系统事件)       │
├─────────────────────────────┼───────────────────────────────┤
│ • 用户偏好中文交流           │ • 2026-04-20 test1 索引优化   │
│ • 用户在北京工作             │ • 2026-04-21 订单服务 v2.3.1  │
│ • 用户使用 AWS               │ • 2026-04-22 test1 CPU 告警   │
├─────────────────────────────┼───────────────────────────────┤
│ 存储: SQLite semantic_memory │ 存储: SQLite events.db        │
│ 检索: 关键词重叠评分         │ 检索: 时间 + 实体 + 类型过滤   │
│ 注入: System Prompt 前缀     │ 注入: User Prompt 后附录       │
│ 影响: Agent 行为风格         │ 影响: 仅作参考，不影响行为      │
└─────────────────────────────┴───────────────────────────────┘
```

---

## 🚀 快速开始

### 方式一：一键部署向导（推荐）

```bash
cd /home/ubuntu/kiro-devops
bash setup.sh
```

向导会自动检测已配置的平台，引导你完成：
- 飞书 App ID / App Secret 配置
- 微信扫码登录（终端显示二维码）
- Webhook 告警接收配置
- 告警推送目标选择（飞书/微信/双平台）
- Dashboard 面板配置
- systemd 服务安装（可选）
- **AWS 凭证检查**（如已安装 boto3，会提示配置 AWS Profile）

### 方式二：手动配置

#### 1. 飞书开放平台配置

1. 打开 https://open.feishu.cn/app 登录
2. 创建企业自建应用，记录 **App ID** 和 **App Secret**
3. 添加「机器人」能力
4. 事件订阅 → 选择 **「使用长连接接收事件」** → 添加 `im.message.receive_v1`
5. 权限管理 → 开通 `im:message`、`im:message:send_as_bot`、`im:resource`
6. 版本管理与发布 → 提交审核 → 发布

> 完整权限列表见 `feishu-auth.json`，最小权限为 `im:message` + `im:message:send_as_bot`。

#### 2. 微信（iLink Bot）配置（可选）

微信接入**无需申请任何开发者账号**，启动时自动扫码登录：

```bash
# 首次启动会显示二维码，微信扫码即可
python3 gateway.py
```

扫码成功后 token 自动保存到 `~/.kiro/weixin_token.json`，下次启动自动读取。

#### 3. 配置本服务

```bash
cp .env.example .env
# 编辑 .env，填入飞书/微信相关配置
```

**Kiro Agent / Skill 目录准备：**

本服务依赖 kiro-cli 的 agent 和 skill 能力，需确保以下目录结构存在：

```
~/.kiro/
├── agents/              # kiro-cli agent 配置文件 (*.json)
│   ├── ec2-alert-analyzer.json
│   ├── eks-alert-analyzer.json
│   └── aws-cost-analyzer.json
└── skills/              # kiro-cli skill 定义文件 (SKILL.md)
    ├── ec2-alert-analyzer/
    │   └── SKILL.md
    ├── eks-alert-check/
    │   └── SKILL.md
    └── aws-cost-analyzer/
        └── SKILL.md
```

- **Agent 配置**：`~/.kiro/agents/*.json` 定义 kiro-cli `--agent` 可用的 agent（prompt、tools、resources）
- **Skill 配置**：`~/.kiro/skills/**/SKILL.md` 定义 trigger 关键词和详细分析流程（告警分类、指标查询模板、输出格式）
- Dashboard 会自动扫描这两个目录展示已安装的 Agents 和 Skills

**可选配置：**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `KIRO_TIMEOUT` | Kiro CLI 同步超时（秒） | `120` |
| `KIRO_ASYNC_TIMEOUT` | 异步任务最长等待（秒） | `1800` |
| `KIRO_AGENT` | 指定 Kiro agent | 空 |
| `ENABLE_MEMORY` | 启用记忆功能 | `false` |
| `DASHBOARD_TOKEN` | Dashboard 访问令牌（留空则关闭面板） | 空 |

#### 4. 启动服务

```bash
# 前台调试（同时启动飞书 + 微信 + Webhook）
python3 gateway.py

# 或使用启动脚本
./start.sh

# systemd 后台（生产）
sudo cp kiro-devops.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kiro-devops
sudo systemctl start kiro-devops
```

#### 5. 查看日志

```bash
sudo journalctl -u kiro-devops -f
```

---

## 💬 多平台支持

kiro-devops 同时支持 **飞书** 和 **微信** 两个沟通渠道。

| 平台 | 连接方式 | 状态 |
|------|----------|------|
| 飞书 | WebSocket 长连接 | ✅ 完整支持（文本/图片/文件）|
| 微信 | iLink Bot API 长轮询 | ✅ 文本消息 / ⚠️ 媒体待支持 |

### 微信接入

微信通过 **iLink Bot API** 接入，**无需申请开发者账号、无需企业认证**。

**接入流程：**

1. 启动服务，终端显示二维码（由 `scripts/setup_weixin.py` 生成）
2. 用微信扫码并确认登录
3. 扫码成功后 `context_token` 自动保存到 `~/.kiro/weixin_token.json`
4. 下次启动自动读取，无需重复扫码

**技术细节：**
- 协议：HTTP JSON API（`ilinkai.weixin.qq.com`）
- 消息接收：35 秒长轮询 `/getupdates`
- 消息发送：`/sendmessage`（需携带 `client_id`、`context_token`、`base_info`）
- 文本分段：单条消息限制 2000 字符，自动分片发送
- **Phase 1 限制**：仅支持文本消息，暂不支持图片/文件上传

### 告警推送策略

- 若 `ALERT_NOTIFY_TARGETS` 配置单一目标，直接推送到对应平台
- 若配置多个目标（逗号分隔），告警会**同时推送到所有目标平台**
- 定时任务：在**哪个平台创建**，结果就推送到**哪个平台**
- 告警分析结果支持**跨平台推送**（如在飞书创建的定时任务，结果可推送到微信）

### 命令兼容性

所有 `/` 命令在飞书和微信中行为一致，但微信暂不支持文件/图片自动上传。

---

## 🧠 记忆系统

记忆功能默认关闭，在 `.env` 中设置 `ENABLE_MEMORY=true` 开启。

**零额外依赖**，全部基于 Python 内置 `sqlite3`。

### 双层记忆

| 类型 | 内容 | 检索 | Prompt 注入 |
|------|------|------|------------|
| **Semantic** | 用户偏好、事实、决策 | 关键词重叠评分 | 前缀注入，影响 Agent 行为 |
| **Episodic** | 系统变更、应用发版、指标异常、故障 | 时间+实体+类型过滤 | 附录在 user prompt 后，标注"仅供参考" |

### 事件录入方式

**方式一：飞书手动录入**

```
/event 类型=系统变更 实体=test1,MySQL 标题="test1 数据库索引优化" 描述="orders 表增加联合索引"
```

**方式二：外部系统 Webhook 推送**

`/event` 接口同时支持两种格式：

**A. 通用事件格式**（Jenkins/Zabbix/Apollo 等自定义系统）

```json
{
  "id": "jenkins-12345",
  "event_type": "应用发版",
  "title": "订单服务 v2.3.1 上线",
  "description": "修复支付回调超时",
  "entities": ["订单服务"],
  "source": "jenkins",
  "severity": "medium",
  "timestamp": "2026-04-25T10:00:00Z",
  "user_id": "feishu:ou_xxx"
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` / `event_id` | ✅ | 业务系统唯一标识，用于幂等去重 |
| `event_type` | ✅ | 事件类型，如「应用发版」「系统变更」「指标异常」 |
| `title` | ✅ | 事件标题 |
| `description` | ❌ | 详细描述 |
| `entities` | ❌ | 关联实体列表，未提供时自动从 title+description 提取 |
| `source` | ❌ | 来源标识，默认 `webhook` |
| `severity` | ❌ | 严重级别 `critical`/`high`/`medium`/`low`，默认 `medium` |
| `timestamp` | ❌ | ISO 格式时间，默认当前时间 |
| `user_id` | ❌ | 归属用户（`feishu:ou_xxx` 或 `weixin:wxid_xxx`），默认 `system` |

> 💡 `user_id` 仅用于事件入库的归属标识，**不影响告警推送目标**。告警推送到哪由 `ALERT_NOTIFY_TARGETS` 控制。

**B. Prometheus Alertmanager 原生格式**

Alertmanager 直接推送的 JSON（含 `alerts` 字段）会被**自动识别并转换**，无需额外适配。详见上文「Prometheus Alertmanager 配置」章节。

### 记忆管理命令

| 命令 | 功能 |
|------|------|
| `/memory status` | 查看记忆开关状态和语义记忆条数 |
| `/memory on` | 开启记忆 |
| `/memory off` | 关闭记忆 |
| `/memory clear` | 清除语义记忆 |
| `/memory events` | 查看最近 30 天事件 |
| `/memory events clear` | 清空事件记录 |

---

## 🚨 EC2 告警自动分析（Webhook + Kiro Skill）

接收 Prometheus/CloudWatch 等监控系统的 EC2 告警，自动触发 Kiro `ec2-alert-analyzer` skill 进行根因分析，并将结果主动推送到配置的目标平台（飞书/微信）。

### 架构流程

```
┌─────────────────┐     webhook POST      ┌─────────────────┐
│  Prometheus     │ ─────────────────────>│                 │
│  Alertmanager   │   Bearer Token + JSON │  kiro-devops│
│  (or CloudWatch │                       │   :8080/event   │
│   via Lambda)   │                       │                 │
└─────────────────┘                       └────────┬────────┘
                                                   │
                              ┌────────────────────┼────────────────────┐
                              ↓                    ↓                    ↓
                       ┌────────────┐      ┌────────────┐      ┌────────────┐
                       │ 事件入库    │      │ severity   │      │ 低级别告警  │
                       │ events.db  │      │ >= high?   │      │ 仅入库      │
                       └────────────┘      └─────┬──────┘      └────────────┘
                                                 │
                                                 ↓ 是
                                    ┌────────────────────────┐
                                    │ Kiro ec2-alert-analyzer│
                                    │   skill 自主查指标      │
                                    │  • CloudWatch CLI      │
                                    │  • Prometheus API      │
                                    │  • 根因诊断 + 建议      │
                                    └───────────┬────────────┘
                                                │
                                                ↓ 分析结果
                                    ┌────────────────────────┐
                                    │  send_message()        │
                                    │  主动推送飞书用户       │
                                    └────────────────────────┘
```

**设计原则**：Bot 只做网关（收告警 → 转给 Kiro → 推结果），所有分析逻辑下沉到 Kiro Skill 中。

### 配置方式

#### 1. 环境变量（`.env`）

```bash
# === Webhook 告警接收 ===
WEBHOOK_ENABLED=true
WEBHOOK_PORT=8080
WEBHOOK_HOST=127.0.0.1          # 127.0.0.1=仅本机, 0.0.0.0=全网卡
WEBHOOK_TOKEN=change-me-secret  # 外部系统（Prometheus Alertmanager / CloudWatch Lambda / Jenkins 等）调用 Webhook 时的 Bearer Token 鉴权，不是聊天用户用的

# === 主动告警推送 ===
ALERT_NOTIFY_USER_ID=ou_xxxxxxxxxxxxxxxx    # Feishu open_id
ALERT_AUTO_ANALYZE_SEVERITY=high,critical   # 哪些级别触发自动分析
ALERT_ANALYZE_TIMEOUT=300                   # Kiro 分析超时（秒）
```

#### 2. Prometheus Alertmanager 配置

```yaml
# alertmanager.yml
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname', 'instance']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h
  receiver: 'kiro-devops'

receivers:
  - name: 'kiro-devops'
    webhook_configs:
      - url: 'http://bot.internal:8080/event'
        http_config:
          bearer_token: 'change-me-secret'
        send_resolved: true
```

Alertmanager 原生推送的 JSON 会被 Bot **自动识别并转换**为标准格式，无需额外适配。

#### 3. CloudWatch 中转（Lambda）

CloudWatch Alarm 通过 SNS → Lambda 中转为 Bot 标准 JSON 格式，详见 `.kiro/skills/ec2-alert-analyzer/SKILL.md`。

### 告警分级响应

| Severity | 行为 |
|----------|------|
| `critical` / `high` | 自动触发 Kiro skill 分析 + 主动推送到配置目标（飞书/微信） |
| `medium` / `low` | 仅入库，用户后续可主动询问 |

### 测试验证

```bash
# 1. 健康检查
curl http://localhost:8080/health
# {"status": "ok", "event_store": true, "webhook": true}

# 2. 模拟 Prometheus Critical 告警（触发自动分析）
curl -X POST http://localhost:8080/event \
  -H "Authorization: Bearer change-me-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "prom-ec2-cpu-001",
    "event_type": "指标异常",
    "title": "test1 EC2 CPU usage > 80%",
    "description": "CPU utilization is 85.2%",
    "entities": ["test1", "i-0abcd1234"],
    "source": "prometheus",
    "severity": "critical",
    "timestamp": "2026-04-23T10:00:00Z"
  }'
# {"ok": true, "event_id": "prom-ec2-cpu-001", "analysis_triggered": true}

# 3. 模拟 Alertmanager 格式
curl -X POST http://localhost:8080/event \
  -H "Authorization: Bearer change-me-secret" \
  -d '{"version":"4","status":"firing","commonLabels":{"alertname":"HighMemoryUsage","instance":"test2:9100","severity":"high"},"commonAnnotations":{"summary":"test2 memory usage > 90%"},"alerts":[{"status":"firing","startsAt":"2026-04-23T11:00:00.000Z"}]}'
# {"ok": true, "event_id": "prom-HighMemoryUsage-2026-04-23T11:00:00", "analysis_triggered": true}

# 4. 低级别告警（不触发分析）
curl ... -d '{"id":"prom-disk-low-001","severity":"low","title":"disk 60%"}'
# {"ok": true, "analysis_triggered": false}

# 5. 鉴权失败
curl -H "Authorization: Bearer wrong-token" ...
# 401 Unauthorized

# 6. 幂等重试
curl ... -d '{"id":"same-id","severity":"low"}'   # 第一次：入库
# 返回 {"ok": true}
curl ... -d '{"id":"same-id","severity":"low"}'   # 第二次：跳过
# 返回 {"ok": true}（数据库无重复记录）
```

### Kiro Skill 自主分析示例

当收到 `test1 EC2 CPU usage > 80%` 告警时，Kiro `ec2-alert-analyzer` skill 会自主执行：

```bash
# 查询 CloudWatch CPU 趋势
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 --metric-name CPUUtilization \
  --dimensions Name=InstanceId,Value=i-0abcd1234 \
  --start-time ... --end-time ... --period 300 --statistics Average Maximum

# 或查询 Prometheus node_exporter
curl -s 'http://prometheus:9090/api/v1/query?query=100-avg(irate(node_cpu_seconds_total{mode="idle",instance=~"test1:9100"}[5m]))*100'
```

最终输出结构化中文报告：

```
═══════════════════════════════════════════════
  EC2 告警分析报告
═══════════════════════════════════════════════

实例:       test1 (i-0abcd1234)
告警类型:    CPU
严重级别:    CRITICAL

【现象】
test1 EC2 CPU usage > 80%

【根因分析】
过去 1 小时 CPU 均值 82%，峰值 91%，持续恶化...

【建议措施】
1. 执行 top / pidstat 定位高 CPU 进程
2. 检查最近是否有新部署
3. 考虑扩容或优化代码

【相关指标】
- CPU Utilization (avg 1h): 82.3%
- Load Average: 4.2 (4 cores)
═══════════════════════════════════════════════
```

---

## 🎯 Alert Mapping 规则引擎（动态 Agent 路由）

默认情况下，所有 Prometheus/CloudWatch 告警都走同一个 Agent 进行分析。通过 **Alert Mapping** 规则引擎，你可以根据告警的多维度特征（alertname、source、severity、labels）将不同告警路由到不同的 Kiro Agent 和 Skill。

### 规则数据结构

规则保存在 `dashboard_config.json` 的 `mappings` 数组中，按**顺序匹配**，第一条满足的规则生效：

```json
{
  "mappings": [
    {
      "name": "k8s-node-notready",
      "enabled": true,
      "match": {
        "source": "prometheus",
        "alertname": "NodeNotReady",
        "severity": ["critical", "high"],
        "labels": { "job": "node-exporter" }
      },
      "action": {
        "agent": "eks-node-analyzer",
        "tools": ["execute_bash", "fs_read", "grep"],
        "timeout": 300,
        "instruction": "分析 K8s Node NotReady 根因，查询 kubectl 和 EC2 状态检查"
      }
    },
    {
      "name": "aws-cost-spike",
      "enabled": true,
      "match": {
        "source": "cloudwatch",
        "alertname": ".*cost.*|.*billing.*"
      },
      "action": {
        "agent": "aws-cost-analyzer",
        "tools": ["execute_bash", "fs_read"],
        "timeout": 300
      }
    }
  ],
  "alert_defaults": {
    "agent": "ec2-alert-analyzer",
    "tools": ["execute_bash"],
    "timeout": 300
  }
}
```

### Match 条件语法

| 条件类型 | 示例 | 说明 |
|----------|------|------|
| 等值匹配 | `"alertname": "NodeNotReady"` | 精确匹配 |
| 正则匹配 | `"alertname": "Node.*\|ExporterDown"` | 自动识别（含 `.*` `\|` `^` `$` 等） |
| 数组 OR | `"severity": ["critical", "high"]` | 满足任一即可 |
| Labels | `"labels": {"job": "node-exporter"}` | 匹配 Prometheus labels |

### 配置热加载

规则引擎支持两种配置刷新方式：

1. **自动热加载**（默认）：基于文件 `mtime` 检测，修改 `dashboard_config.json` 后 **1 秒内**自动生效，无需重启服务
2. **手动 Reload**：Dashboard Alert Mappings 页面点击 **🔄 Reload Agent** 按钮，立即强制刷新配置

### Dashboard 管理

打开 `http://<服务器IP>:8080/dashboard/` → **Config** → **Alert Mappings**：

- **规则卡片**：每条规则独立卡片，显示 Match 条件和 Action 配置
- **启用/停用**：开关切换，停用规则会被跳过
- **规则排序**：上下箭头调整优先级（顺序匹配）
- **Severity / Tools 多选**：checkbox 组选择多个值
- **Labels 动态列表**：可添加/删除任意 label 键值对
- **Fallback Defaults**：未匹配时的默认 agent/tools/timeout

### 向后兼容

旧版扁平格式 `{source, service, severity, agent, skill}` 会自动迁移为新格式，不影响现有配置。

---

## 💬 多轮对话

### 会话自动延续

Bot 默认会自动延续同一话题的上下文。如果 **30 分钟内**继续发消息，会自动 resume 到同一会话，Kiro CLI 会携带完整历史上下文进行推理。

### 显式会话管理

当需要切换话题时，使用以下命令：

| 命令 | 说明 |
|------|------|
| `/new` | 强制开启新会话，下条消息不受历史上下文影响 |
| `/sessions` | 查看最近 10 个历史会话 |
| `/resume <编号>` | 恢复某个历史会话，继续之前的对话 |

> 💡 **提示**：如果 Bot 的回复偏离了当前话题（比如 resume 到了旧会话），发送 `/new` 即可重置。

---

## ⏰ 定时任务

通过自然语言配置周期性任务，Bot 会在指定时间自动执行 Kiro 指令并将结果推送给你。

**用法示例：**
```
/schedule 每天上午9点检查 AWS 费用
/schedule 每周一凌晨2点备份数据库
/schedule 每30分钟检查 EC2 实例状态
```

**管理命令：**
```
/schedule list      # 列出所有定时任务
/schedule delete 1  # 删除编号 1 的任务
/schedule help      # 查看帮助
```

---

## 🖥️ Web Dashboard

基于 Vue 3 的单页管理面板，通过浏览器可视化查看和管理 Bot 运行状态。

### 访问方式

```
http://<服务器IP>:8080/dashboard/
```

### 登录

- **无需账号**，仅需输入 `.env` 中设置的 `DASHBOARD_TOKEN`
- 登录成功后 24h 内免重新输入（HttpOnly Cookie）
- 留空 `DASHBOARD_TOKEN` 则关闭面板（返回 503）

### 功能页面

| 页面 | 功能 |
|------|------|
| **总览** | Events / Active Jobs / Agents / Skills 数量卡片 |
| **Agents** | 扫描 `~/.kiro/agents/*.json`，展示名称、描述、工具 |
| **Skills** | 扫描 `~/.kiro/skills/**/SKILL.md`，展示名称、描述、触发词 |
| **Events** | 事件列表（支持按 severity/source/关键词过滤）、新增、删除 |
| **Scheduler** | 定时任务 CRUD（启用/禁用/编辑/删除） |
| **Resources** | AWS EC2 / RDS 资源自动发现 + CloudWatch 指标 |
| **Config** | Core 环境变量编辑 + Alert-to-Agent 映射规则管理 |

### Alert Mappings

在 **Config → Alert Mappings** 标签页配置告警路由规则：

```json
[
  { "source": "prometheus", "severity": "high",     "agent": "ec2-alert-analyzer" },
  { "source": "prometheus", "severity": "critical", "agent": "ec2-alert-analyzer" }
]
```

当前 webhook 告警路由为硬编码（`ec2-alert-analyzer`），Dashboard Mappings 用于可视化展示配置，后续可扩展为动态路由。

### Resources（AWS 资源监控）

Dashboard 自动发现 AWS 资源并展示 CloudWatch 指标：

| 资源类型 | 发现方式 | 指标 |
|----------|----------|------|
| **EC2** | boto3 `describe_instances` | CPUUtilization（7天/30天） |
| **RDS** | boto3 `describe_db_instances` | CPUUtilization（7天/30天） |

**展示内容：**
- 资源列表（名称、实例类型、状态、区域）
- Sparkline 迷你趋势图（7 天 CPU 均值）
- 统计卡片：7天 avg / p95 / max，30天 avg / p95 / max
- 置顶（Pin）常用资源，置顶项优先显示

**前置条件：**
1. 安装 `boto3`：`pip3 install boto3`
2. **配置 AWS Profile 或凭证（必须先完成）**：
   - 推荐：`aws configure` 配置标准凭证文件 `~/.aws/credentials`
   - 或在 `.env` 中设置 `AWS_ACCESS_KEY_ID` 和 `AWS_SECRET_ACCESS_KEY`
   - 或在 EC2 实例上挂载 IAM Role
   - 若使用非 default 的 Profile，可在 `.env` 中设置 `AWS_PROFILE=your-profile-name`
3. **IAM 权限范围（最小只读权限）**：
   - `ec2:DescribeInstances`
   - `rds:DescribeDBInstances`
   - `cloudwatch:GetMetricStatistics`
   - `cloudwatch:ListMetrics`

> ⚠️ **安全提示**：请勿使用具有写权限（如 `*:*`）的 Admin 凭证。建议为 kiro-devops 单独创建只读 IAM User / Role，并限制最小权限。

**缓存策略：** 5 分钟 TTL，支持手动刷新。

---

## 📎 图片与文件发送

Bot 支持自动检测 Kiro 输出中的文件路径，并上传到飞书发送：

- **图片**：`.png` `.jpg` `.jpeg` `.gif` `.bmp` `.webp` → 以图片消息回复
- **文件**：`.pdf` `.doc` `.docx` `.xls` `.xlsx` `.ppt` `.pptx` `.csv` `.txt` `.zip` `.mp4` → 以文件消息回复

**工作流程：**
1. 你向 Bot 发送请求（如"生成 CPU 趋势图"）
2. Kiro 处理并生成文件，输出中包含绝对路径（如 `/tmp/report/cpu.png`）
3. Bot 自动检测到存在的文件路径
4. 上传至飞书，以图片/文件消息回复

> **注意**：需在飞书开放平台开通 `im:resource` 权限（上传图片/文件）。

---

## ⌨️ 命令参考

| 命令 | 说明 |
|------|------|
| `/new` | 强制开启新会话 |
| `/resume <编号>` | 恢复历史会话 |
| `/sessions` | 列出历史会话 |
| `/status` | 查看后台任务状态 |
| `/cancel` | 取消后台任务 |
| `/schedule` | 定时任务管理 |
| `/memory` | 记忆管理 |
| `/event` | 手动录入事件 |

---

## 📦 依赖

### 必需

| 依赖 | 说明 | 安装方式 |
|------|------|----------|
| **kiro-cli** | Kiro 核心 CLI，Bot 通过子进程调用它处理所有消息 | [kiro.dev](https://kiro.dev) 官方安装 |
| **lark-oapi** | 飞书 SDK | `pip3 install lark-oapi` |
| **flask** | Webhook HTTP 服务 + Dashboard | `pip3 install flask` |
| **qrcode** | 微信扫码登录二维码生成 | `pip3 install qrcode[pil]` |
| **schedule** | 定时任务调度 | `pip3 install schedule` |

一次性安装 Python 依赖：

```bash
pip3 install -r requirements.txt
```

### 可选

| 依赖 | 用途 | 安装命令 |
|------|------|----------|
| **boto3** | Dashboard Resources（AWS EC2/RDS 自动发现 + CloudWatch 指标） | `pip3 install boto3` |
| **awscli** | Kiro Skill 中执行 AWS CLI 命令（如 CloudWatch 查询） | `pip3 install awscli` 或 `apt install awscli` |

**AWS 凭证配置（使用 boto3 / awscli 前必须完成）：**

```bash
# 方式一：aws configure 配置标准凭证（推荐）
aws configure
# 按提示输入 Access Key / Secret Key / Region / Output format

# 方式二：.env 中显式指定（适合容器或 CI）
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=xxx
AWS_DEFAULT_REGION=ap-northeast-1

# 方式三：使用非 default Profile
AWS_PROFILE=production
```

> 💡 如果只需要飞书/微信聊天和定时任务功能，无需安装 `boto3` 和 `awscli`。
> ⚠️ 使用 AWS 功能前，请务必确认当前 AWS Profile 的 IAM 权限范围，建议仅授予 ReadOnly 权限。

---

## 📄 许可证

[MIT](LICENSE)
