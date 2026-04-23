# 飞书 ↔ Kiro Bot 桥接服务

[![DeepWiki](https://img.shields.io/badge/DeepWiki-AI%20文档-blue)](https://deepwiki.com/wchengyen/feishu-kiro-bot)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[English Version](README_EN.md) | 中文版

在飞书（Lark）中 @机器人 发消息，自动调用 [Kiro CLI](https://kiro.dev) 处理并回复结果。

**无需公网 IP、无需端口开放、无需 nginx 反向代理。**

---

## 📖 AI 生成的交互式文档

👉 **[https://deepwiki.com/wchengyen/feishu-kiro-bot](https://deepwiki.com/wchengyen/feishu-kiro-bot)**

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

---

## 🏗️ 架构概览

```
飞书用户 @Bot "test1 数据库怎么了？"
       ↓
飞书云 ←—WebSocket 出站长连接—→ 本服务 (Python)
                                    ↓
                              ┌─────────────┐
                              │ Event Loop  │  lark-oapi 接收消息
                              └─────────────┘
                                    ↓
                              ┌─────────────┐
                              │ 启发式判断   │  has_episodic_hint()
                              └─────────────┘
                                    ↓
            ┌───────────────────────┼───────────────────────┐
            ↓                       ↓                       ↓
    ┌───────────────┐      ┌───────────────┐      ┌───────────────┐
    │ SemanticStore │      │ EventStore    │      │ SessionRouter │
    │ SQLite FTS5   │      │ SQLite + FTS5 │      │ 会话超时管理   │
    └───────────────┘      └───────────────┘      └───────────────┘
            ↓                       ↓                       ↓
            └───────────────────────┼───────────────────────┘
                                    ↓
                              ┌─────────────┐
                              │PromptBuilder│  Semantic→前缀  Episodic→附录
                              └─────────────┘
                                    ↓
                              ┌─────────────┐
                              │KiroExecutor │  subprocess kiro-cli
                              │同步/异步切换 │  --resume --no-interactive
                              └─────────────┘
                                    ↓
                              飞书用户收到回复
                              📎 本次分析关联了 2 条历史事件
```

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

### 1. 飞书开放平台配置

1. 打开 https://open.feishu.cn/app 登录
2. 创建企业自建应用，记录 **App ID** 和 **App Secret**
3. 添加「机器人」能力
4. 事件订阅 → 选择 **「使用长连接接收事件」** → 添加 `im.message.receive_v1`
5. 权限管理 → 开通 `im:message`、`im:message:send_as_bot`、`im:resource`
6. 版本管理与发布 → 提交审核 → 发布

> 完整权限列表见 `feishu-auth.json`，最小权限为 `im:message` + `im:message:send_as_bot`。

### 2. 配置本服务

```bash
cd /home/ubuntu/feishu-kiro-bot
cp .env.example .env
# 编辑 .env，填入 FEISHU_APP_ID 和 FEISHU_APP_SECRET
```

**可选配置：**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `KIRO_TIMEOUT` | Kiro CLI 同步超时（秒） | `120` |
| `KIRO_ASYNC_TIMEOUT` | 异步任务最长等待（秒） | `1800` |
| `KIRO_AGENT` | 指定 Kiro agent | 空 |
| `ENABLE_MEMORY` | 启用记忆功能 | `false` |

### 3. 启动服务

```bash
# 前台调试
./start.sh

# systemd 后台（生产）
sudo cp feishu-kiro-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable feishu-kiro-bot
sudo systemctl start feishu-kiro-bot
```

### 4. 查看日志

```bash
sudo journalctl -u feishu-kiro-bot -f
```

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

```json
{
  "id": "jenkins-12345",
  "event_type": "应用发版",
  "title": "订单服务 v2.3.1 上线",
  "description": "修复支付回调超时",
  "entities": ["订单服务"],
  "source": "jenkins",
  "user_id": "ou_xxx"
}
```

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

```bash
# 唯一必需依赖
pip3 install lark-oapi
```

> 记忆功能为零额外依赖（基于 sqlite3）。如需恢复旧版向量记忆，需手动安装 `chromadb sentence-transformers`。

---

## 🧪 测试

```bash
# 全部测试
python3 test_event_store.py
python3 test_memory.py
python3 test_prompt_builder.py
python3 test_event_ingest.py
python3 test_step3_integration.py
python3 test_step5_integration.py
python3 test_performance.py
python3 test_rollback.py
```

---

## 🔄 回退方案

如需恢复改造前的 ChromaDB 版本：

```bash
sudo systemctl stop feishu-kiro-bot
git reset --hard backup/before-memory-rewrite
# 重新安装旧依赖：pip3 install chromadb sentence-transformers
sudo systemctl start feishu-kiro-bot
```

---

## 📄 许可证

[MIT](LICENSE)
