# 飞书 / 微信 ↔ Kiro Bot 桥接服务

<p align="center">
  <img src="kiro2.jpg" alt="Kiro Bot" width="180">
</p>

[![DeepWiki](https://img.shields.io/badge/DeepWiki-AI%20文档-blue)](https://deepwiki.com/wchengyen/kiro-devops)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[English Version](README_EN.md) | 中文版

📖 [DeepWiki AI 文档](https://deepwiki.com/wchengyen/kiro-devops)

在**飞书（Lark）**或**微信（iLink Bot）**中发消息，自动调用 [Kiro CLI](https://kiro.dev) 处理并回复结果。

**无需公网 IP、无需端口开放、无需 nginx 反向代理。**

> 💡 **单实例同时运行双平台**：通过 `PlatformAdapter` 抽象层，一套业务代码同时服务飞书和微信用户。

---

## 1. 多平台連接：微信 / 飛書雙平台

kiro-devops 同时支持 **飞书** 和 **微信** 两个沟通渠道。

| 平台 | 连接方式 | 状态 |
|------|----------|------|
| 飞书 | WebSocket 长连接 + 群历史消息轮询 | ✅ 完整支持（文本/图片/文件）|
| 微信 | iLink Bot API 长轮询 | ✅ 文本消息 / ⚠️ 媒体待支持 |

### 1.1 飞书接入

1. 打开 https://open.feishu.cn/app 登录
2. 创建企业自建应用，记录 **App ID** 和 **App Secret**
3. 添加「机器人」能力，并将 Bot 加入目标群聊
4. 事件订阅 → 选择 **「使用长连接接收事件」** → 添加 `im.message.receive_v1`
5. 权限管理 → 开通：
   - `im:message`（收发消息）
   - `im:message:send_as_bot`（以 Bot 身份发送）
   - `im:resource`（上传图片/文件）
   - **`im:message:group:readonly`**（读取群消息，群轮询必需）
6. 版本管理与发布 → 提交审核 → 发布

> 完整权限列表见 `feishu-auth.json`，最小权限为 `im:message` + `im:message:send_as_bot`。
>
> 💡 **群历史消息轮询**：若需收取群内其他 Bot（如 Prometheus、Zabbix）的告警消息，请在 `.env` 中配置 `FEISHU_POLL_CHAT_IDS`（见 7.3 节）。

### 1.2 微信接入

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

### 1.3 平台适配器架构

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
   ┌─────────────────────┐     ┌─────────────┐     ┌─────────────┐
   │    FeishuAdapter    │     │WeixinAdapter│     │  (未来扩展)  │
   │ lark-oapi WS + Poll │     │iLink HTTP   │     │ 钉钉/Slack  │
   └─────────────────────┘     └─────────────┘     └─────────────┘
```

| 设计点 | 说明 |
|--------|------|
| **统一用户 ID** | `platform:raw_id`（如 `feishu:ou_xxx`、`weixin:wxid_xxx@im.wechat`），无需跨平台绑定 |
| **平台无关业务核心** | `MessageHandler` 处理所有 `/` 命令，完全不知道消息来自哪个平台 |
| **统一发送路由** | `PlatformDispatcher` 按 `platform:raw_id` 前缀分发到对应适配器 |
| **上下文隔离** | 微信的 `context_token` 由适配器内部维护，业务层无感知 |
| **渐进式扩展** | 新增平台只需实现 `PlatformAdapter` 三个方法，零侵入业务代码 |

---

## 2. 會話管理：同步異步處理 / 會話路由管理

### 2.1 混合執行引擎

| 模式 | 說明 | 超時 |
|------|------|------|
| **同步** | 用戶發送消息後，Bot 實時調用 kiro-cli 處理，等待回復 | 120 秒 |
| **異步** | 同步超時後自動轉後台線程，帶進度心跳，完成後主動推送 | 最長 1800 秒 |

```
用戶發送消息
    ↓
KiroExecutor.execute()
    ↓
同步執行（120s）
    ├── 完成 → 立即回復
    └── 超時 → 轉後台異步線程
              ↓
        用戶收到：「任務較複雜，已轉入後台處理」
              ↓
        後台完成 → PlatformDispatcher 主動推送結果
```

### 2.2 會話自動延續

Bot 默認會自動延續同一話題的上下文。如果 **30 分鐘內**繼續發消息，會自動 resume 到同一會話，Kiro CLI 會攜帶完整歷史上下文進行推理。

### 2.3 顯式會話管理

| 命令 | 說明 |
|------|------|
| `/new` | 強制開啟新會話，下條消息不受歷史上下文影響 |
| `/sessions` | 查看最近 10 個歷史會話 |
| `/resume <編號>` | 恢復某個歷史會話，繼續之前的對話 |
| `/status` | 查看後台任務狀態 |
| `/cancel` | 取消後台任務 |

> 💡 **提示**：如果 Bot 的回復偏離了當前話題，發送 `/new` 即可重置。

---

## 3. 定時任務支持

通過自然語言配置週期性任務，Bot 會在指定時間自動執行 Kiro 指令並將結果推送給你。

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

## 4. 事件接入：Webhook 接入

`/event` 接口同時支持兩種格式：

### 4.1 Prometheus Alertmanager 原生格式

Alertmanager 直接推送的 JSON（含 `alerts` 字段）會被**自動識別並轉換**，無需額外適配。

```yaml
# alertmanager.yml
receivers:
  - name: 'kiro-devops'
    webhook_configs:
      - url: 'http://bot.internal:8080/event'
        http_config:
          bearer_token: 'change-me-secret'
        send_resolved: true
```

### 4.2 通用事件格式（Jenkins / Zabbix / Apollo 等）

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

| 字段 | 必填 | 說明 |
|------|------|------|
| `id` / `event_id` | ✅ | 業務系統唯一標識，用於冪等去重 |
| `event_type` | ✅ | 事件類型，如「應用發版」「系統變更」「指標異常」 |
| `title` | ✅ | 事件標題 |
| `description` | ❌ | 詳細描述 |
| `entities` | ❌ | 關聯實體列表，未提供時自動從 title+description 提取 |
| `source` | ❌ | 來源標識，默認 `webhook` |
| `severity` | ❌ | `critical` / `high` / `medium` / `low`，默認 `medium` |
| `timestamp` | ❌ | ISO 格式時間，默認當前時間 |
| `user_id` | ❌ | 歸屬用戶，默認 `system` |

---

## 5. 事件記錄

### 5.1 雙層記憶架構

記憶功能默認關閉，在 `.env` 中設置 `ENABLE_MEMORY=true` 開啟。

**零額外依賴**，全部基於 Python 內置 `sqlite3`。

| 類型 | 內容 | 存儲 | 檢索 |
|------|------|------|------|
| **Semantic Memory** | 用戶偏好、事實、決策 | `semantic_memory.db` | 關鍵詞重疊評分 |
| **Episodic Memory** | 系統變更、應用發版、指標異常、故障 | `events.db` | 時間 + 實體 + 類型過濾 |

### 5.2 事件錄入方式

**方式一：飛書手動錄入**
```
/event 类型=系统变更 实体=test1,MySQL 标题="test1 数据库索引优化" 描述="orders 表增加联合索引"
```

**方式二：Webhook 外部系統推送**（見第 4 章）

**方式三：群消息自動識別**

飛書群內發送結構化告警消息，無需 `@` 機器人，系統自動識別並入庫：
- 中文鍵值對：`告警名稱：xxx`、`告警級別：critical`、`命名空間：content`
- 英文鍵值對：`alertname: xxx`、`severity: high`、`namespace: kube-system`
- JSON 格式：`{"title":"xxx","severity":"critical"}`

啟用方式：`.env` 中設置 `GROUP_ALERT_LISTEN_ENABLED=true`

### 5.3 記憶管理命令

| 命令 | 功能 |
|------|------|
| `/memory status` | 查看記憶狀態 |
| `/memory on` | 開啟記憶 |
| `/memory off` | 關閉記憶 |
| `/memory events` | 查看最近事件 |
| `/memory events clear` | 清空事件記錄 |

---

## 6. 自動 AIOPS 分析處理

### 6.1 告警分級響應

| Severity | 行為 |
|----------|------|
| `critical` / `high` | 依照 Alert Policy 匹配規則，自動路由到對應 Kiro Agent + Skill 分析，結果主動推送 |
| `medium` / `low` | 僅入庫，用戶後續可主動詢問 |

### 6.2 Alert Mapping 規則引擎（動態 Agent 路由）

根據告警的多維度特徵（`alertname`、`source`、`severity`、`labels`）將不同告警路由到不同的 Kiro Agent 和 Skill。

規則保存在 `dashboard_config.json` 的 `mappings` 數組中，按**順序匹配**，第一條滿足的規則生效：

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
        "instruction": "分析 K8s Node NotReady 根因"
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

**Match 條件語法：**

| 條件類型 | 示例 |
|----------|------|
| 等值匹配 | `"alertname": "NodeNotReady"` |
| 正則匹配 | `"alertname": "Node.*\\|ExporterDown"` |
| 數組 OR | `"severity": ["critical", "high"]` |
| Labels | `"labels": {"job": "node-exporter"}` |

**配置熱加載：** 修改 `dashboard_config.json` 後 **1 秒內**自動生效，無需重啟服務。

### 6.3 群消息告警監聽

除 Webhook 推送外，Bot 支持**直接在飛書群內監聽結構化告警消息**，無需 `@` 機器人即可觸發自動分析，結果直接回復到原群。

**消息來源：**
- 用戶在群內發送的告警文本
- **其他 Bot（如 Prometheus、Zabbix Bot）通過 Webhook 推送到群內的告警**

**技術實現：**
- WebSocket 事件僅推送實時消息（私聊 / @Bot）
- 群內其他 Bot 的消息通過 **List Message API 輪詢** 補收（見 `FEISHU_POLL_CHAT_IDS` 配置）

```
群消息（無需 @）
    ↓
FeishuAdapter 輪詢拉取（或 WS 實時推送）
    ↓
MessageHandler._parse_structured_alert()
    ↓
提取 title + severity + namespace + pod + instance
    ↓
severity ∈ {high, critical} ?
    ├── 是 → AlertMatcher.match() → run_alert_analysis() → 原群回復
    └── 否 → 入庫，靜默忽略
```

### 6.4 Kiro Skill 自主分析示例

當收到 `test1 EC2 CPU usage > 80%` 告警時，Kiro `ec2-alert-analyzer` skill 會自主執行：

```bash
# 查詢 CloudWatch CPU 趨勢
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 --metric-name CPUUtilization \
  --dimensions Name=InstanceId,Value=i-0abcd1234 \
  --start-time ... --end-time ... --period 300 --statistics Average Maximum

# 或查詢 Prometheus node_exporter
curl -s 'http://prometheus:9090/api/v1/query?query=...'
```

最終輸出結構化中文報告（現象、根因分析、建議措施、相關指標）。

---

## 7. Setup / Start 腳本

### 7.1 一鍵部署嚮導（推薦）

```bash
cd /home/ubuntu/kiro-devops

# 建立虚拟环境（现代 Ubuntu/Debian 因 PEP 668 必需）
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 启动交互式配置向导
bash setup.sh
```

嚮導會自動檢測已配置的平台，引導完成：
- 飛書 App ID / App Secret 配置
- 微信掃碼登錄
- Webhook 告警接收配置
- 告警推送目標選擇
- Dashboard 面板配置
- systemd 服務安裝（可選）
- AWS 憑證檢查

### 7.2 Kiro Agent / Skill 目錄準備

```
~/.kiro/
├── agents/              # kiro-cli agent 配置文件 (*.json)
│   ├── ec2-alert-analyzer.json
│   ├── eks-alert-analyzer.json
│   └── aws-cost-analyzer.json
└── skills/              # kiro-cli skill 定義文件 (SKILL.md)
    ├── ec2-alert-analyzer/
    │   └── SKILL.md
    ├── eks-alert-analyzer/
    │   └── SKILL.md
    └── aws-cost-analyzer/
        └── SKILL.md
```

### 7.3 環境變量配置

```bash
cp .env.example .env
# 編輯 .env，填入飛書/微信相關配置
```

| 變量 | 說明 | 默認值 |
|------|------|--------|
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 飛書應用憑證 | 空 |
| `FEISHU_POLL_CHAT_IDS` | 輪詢群 chat_id（逗號分隔），用於收取其他 Bot 消息 | 空 |
| `FEISHU_POLL_INTERVAL_SEC` | 群消息輪詢間隔（秒） | `10` |
| `WEIXIN_BOT_TOKEN` | 微信 iLink Bot Token | 空 |
| `KIRO_TIMEOUT` | Kiro CLI 同步超時（秒） | `120` |
| `KIRO_AGENT` | 指定 Kiro agent | 空 |
| `ENABLE_MEMORY` | 啟用記憶功能 | `false` |
| `GROUP_ALERT_LISTEN_ENABLED` | 群消息告警監聽 | `false` |
| `ALERT_AUTO_ANALYZE_SEVERITY` | 自動分析級別 | `high,critical` |
| `ALERT_ANALYZE_TIMEOUT` | Kiro 分析超時（秒） | `90` |
| `DASHBOARD_TOKEN` | Dashboard 訪問令牌 | 空 |

### 7.4 啟動服務

```bash
# 前台調試
python3 gateway.py

# 或使用啟動腳本
./start.sh

# systemd 後台（生產）
sudo cp kiro-devops.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kiro-devops
sudo systemctl start kiro-devops
```

### 7.5 查看日誌

```bash
sudo journalctl -u kiro-devops -f
```

---

## 8. Dashboard 監控看板

基於 Vue 3 的單頁管理面板，通過瀏覽器可視化查看和管理 Bot 運行狀態。

```
http://<服務器IP>:8080/dashboard/
```

- **無需賬號**，僅需輸入 `.env` 中設置的 `DASHBOARD_TOKEN`
- 登錄成功後 24h 內免重新輸入（HttpOnly Cookie）
- 留空 `DASHBOARD_TOKEN` 則關閉面板（返回 503）

### 8.1 Agent 列表

掃描 `~/.kiro/agents/*.json`，展示：
- Agent 名稱、描述
- 可用工具（tools）
- 引用的 Skill 資源

### 8.2 Skill 列表

掃描 `~/.kiro/skills/**/SKILL.md`，展示：
- Skill 名稱、描述、版本
- 觸發詞（triggers）
- 診斷流程模板

### 8.3 事件列表

- 事件列表（支持按 `severity` / `source` / 關鍵詞過濾）
- 新增、刪除事件
- 分級響應標準說明

### 8.4 定時任務列表

- 定時任務 CRUD（啟用 / 禁用 / 編輯 / 刪除）
- 自然語言配置週期性任務

### 8.5 資源列表

AWS EC2 / RDS 資源自動發現 + CloudWatch 指標：

| 資源類型 | 發現方式 | 指標 |
|----------|----------|------|
| **EC2** | boto3 `describe_instances` | CPUUtilization（7天/30天） |
| **RDS** | boto3 `describe_db_instances` | CPUUtilization（7天/30天） |

- Sparkline 迷你趨勢圖（7 天 CPU 均值）
- 統計卡片：7天 avg / p95 / max，30天 avg / p95 / max
- 置頂（Pin）常用資源

### 8.6 規則配置設定

#### 8.6.1 Env 設定

在 **Config** 標籤頁編輯 Core 環境變量：
- `KIRO_AGENT`
- `ALERT_AUTO_ANALYZE_SEVERITY`
- `ALERT_ANALYZE_TIMEOUT`
- `GROUP_ALERT_LISTEN_ENABLED`
- `GROUP_AT_ONLY`
- `ENABLE_MEMORY`
- `DEFAULT_MODEL` / `BACKGROUND_MODEL`

#### 8.6.2 匹配規則設定（Alert Mappings）

在 **Config → Alert Mappings** 標籤頁配置告警路由規則：

- **規則卡片**：每條規則獨立卡片，顯示 Match 條件和 Action 配置
- **啟用/停用**：開關切換，停用規則會被跳過
- **規則排序**：上下箭頭調整優先級（順序匹配）
- **Severity / Tools 多選**：checkbox 組選擇多個值
- **Labels 動態列表**：可添加/刪除任意 label 鍵值對
- **Fallback Defaults**：未匹配時的默認 agent/tools/timeout

---

## 命令參考

| 命令 | 說明 |
|------|------|
| `/new` | 強制開啟新會話 |
| `/resume <編號>` | 恢復歷史會話 |
| `/sessions` | 列出歷史會話 |
| `/status` | 查看後台任務狀態 |
| `/cancel` | 取消後台任務 |
| `/schedule` | 定時任務管理 |
| `/memory` | 記憶管理 |
| `/event` | 手動錄入事件 |

---

## 依賴

### 必需

| 依賴 | 說明 | 安裝方式 |
|------|------|----------|
| **kiro-cli** | Kiro 核心 CLI | [kiro.dev](https://kiro.dev) |
| **lark-oapi** | 飛書 SDK | `pip install lark-oapi` |
| **flask** | Webhook HTTP + Dashboard | `pip install flask` |
| **qrcode** | 微信掃碼二維碼 | `pip install qrcode[pil]` |
| **schedule** | 定時任務調度 | `pip install schedule` |
| **python-dotenv** | 环境变量加载 | `pip install python-dotenv` |
| **pyyaml** | YAML 解析 | `pip install pyyaml` |
| **pytz** | 时区支持 | `pip install pytz` |
| **boto3** | AWS 资源发现（实际必需） | `pip install boto3` |

### 可選

| 依賴 | 用途 | 安裝命令 |
|------|------|----------|
| **boto3** | AWS 資源自動發現 + CloudWatch | `pip3 install boto3` |
| **awscli** | Kiro Skill 中執行 AWS CLI | `pip3 install awscli` |

---

## 許可證

[MIT](LICENSE)
