# 飞书 ↔ Kiro CLI 桥接服务（WebSocket 长连接版）

在飞书中 @机器人 发消息，自动调用 Kiro CLI 处理并回复结果。

**无需公网 IP、无需端口开放、无需 nginx 反向代理。**

## 架构

```
飞书用户 @Bot "分析一下成本"
       ↓
飞书云 ←—WebSocket 长连接—→ 本服务 (Python)
                                ↓
                           SessionRouter（会话路由）
                           30min 内自动 resume / 显式命令
                                ↓
                           KiroExecutor（混合执行引擎）
                           同步 120s → 超时转异步 600s
                                ↓ subprocess
                           kiro-cli chat [--resume] --no-interactive "分析一下成本"
                                ↓
                           飞书用户收到回复
                           📎 会话 #1 分析一下成本 | /new 新对话
```

服务主动向飞书建立 WebSocket 出站连接，飞书通过该连接推送事件。连接方向是出站的，所以：
- 不需要公网 IP
- 不需要开放端口
- 不需要处理飞书 IP 白名单
- 天然穿透 NAT / 防火墙

## 部署步骤

### 第一步：飞书开放平台创建应用

1. 打开 https://open.feishu.cn/app 登录
2. 点击「创建企业自建应用」
3. 记录 `App ID` 和 `App Secret`

### 第二步：添加机器人能力 & 权限

1. 应用详情 → 「添加应用能力」→ 选择「机器人」
2. 「权限管理」→ 开通以下权限：

**应用权限（tenant）：**

| 权限 | 说明 |
|------|------|
| `im:message` | 获取与发送消息 |
| `im:message:send_as_bot` | 以应用身份发送消息 |
| `im:message:readonly` | 读取消息 |
| `im:message.p2p_msg:readonly` | 读取私聊消息 |
| `im:message.group_msg` | 获取群组消息 |
| `im:message.group_at_msg:readonly` | 读取群内 @机器人消息 |
| `im:chat` | 获取与管理群组 |
| `im:chat.members:bot_access` | 获取群成员（机器人所在群） |
| `im:chat.access_event.bot_p2p_chat:read` | 读取机器人单聊事件 |
| `im:resource` | 上传图片/文件资源 |
| `contact:contact.base:readonly` | 读取通讯录基本信息 |
| `contact:user.employee_id:readonly` | 读取用户工号 |
| `docs:document.content:read` | 读取文档内容 |
| `sheets:spreadsheet` | 读写电子表格 |
| `wiki:wiki:readonly` | 读取知识库 |
| `aily:file:read` | 读取智能伙伴文件 |
| `aily:file:write` | 写入智能伙伴文件 |
| `cardkit:card:write` | 发送卡片消息 |
| `corehr:file:download` | 下载人事文件 |
| `application:application.app_message_stats.overview:readonly` | 读取应用消息统计 |
| `application:application:self_manage` | 应用自管理 |
| `application:bot.menu:write` | 配置机器人菜单 |
| `event:ip_list` | 获取事件 IP 列表 |

**用户权限（user）：**

| 权限 | 说明 |
|------|------|
| `aily:file:read` | 读取智能伙伴文件 |
| `aily:file:write` | 写入智能伙伴文件 |
| `im:chat.access_event.bot_p2p_chat:read` | 读取机器人单聊事件 |

> **提示**：完整权限列表见 `feishu-auth.json`。最小可用权限为 `im:message` + `im:message:send_as_bot`，其余按需开通。

### 第三步：配置事件订阅（关键！）

1. 应用详情 → 「事件与回调」→ 「事件配置」
2. **接收方式选择「使用长连接接收事件」**（不是 Webhook URL）
3. 添加事件：`im.message.receive_v1`（接收消息）

### 第四步：发布应用

1. 「版本管理与发布」→ 创建版本 → 提交审核 → 发布

### 第五步：配置本服务

```bash
cd /home/ubuntu/feishu-kiro-bot
cp .env.example .env
vim .env   # 填入 APP_ID、APP_SECRET
```

**可选配置项：**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `KIRO_TIMEOUT` | Kiro CLI 同步超时时间（秒） | `120` |
| `KIRO_ASYNC_TIMEOUT` | 异步任务最长等待时间（秒） | `1800` |
| `KIRO_SYNC_TIMEOUT` | 同步阶段超时时间（秒） | `120` |
| `KIRO_PROGRESS_INTERVAL` | 异步任务进度汇报间隔（秒） | `300` |
| `KIRO_AGENT` | 指定 Kiro agent，留空使用默认 agent | 空 |
| `ENABLE_MEMORY` | 启用记忆功能 | `false` |

### 第六步：启动服务

```bash
# 前台运行（调试用）
./start.sh

# systemd 后台运行（生产用）
sudo cp feishu-kiro-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable feishu-kiro-bot
sudo systemctl start feishu-kiro-bot
```

## 依赖

```bash
# 核心依赖（必装）
pip3 install lark-oapi

# 记忆功能依赖（可选，仅 ENABLE_MEMORY=true 时需要）
pip3 install chromadb sentence-transformers
```

## 图片与文件发送

Bot 支持自动检测 Kiro 输出中的文件路径，上传并发送到飞书：

- **图片**：`.png` `.jpg` `.jpeg` `.gif` `.bmp` `.webp` → 以图片消息回复
- **文件**：`.pdf` `.doc` `.docx` `.xls` `.xlsx` `.ppt` `.pptx` `.csv` `.txt` `.zip` `.mp4` → 以文件消息回复

**工作流程：**

1. Kiro 处理用户请求，生成图表或文件（如 EC2 CPU 趋势图）
2. Bot 先回复文本内容
3. 自动扫描文本中的绝对路径（如 `/tmp/report/cpu.png`）
4. 检测到存在的文件 → 上传到飞书 → 以图片/文件消息回复

> **注意**：飞书应用需开通 `im:resource` 权限（上传图片/文件）。在飞书开放平台「权限管理」中添加。

## 记忆功能（可选）

记忆功能默认**关闭**，无需安装额外依赖即可运行 Bot。如需启用，在 `.env` 中设置：

```bash
ENABLE_MEMORY=true
```

并安装记忆依赖：

```bash
pip3 install chromadb sentence-transformers
```

Bot 集成了基于 ChromaDB 的向量记忆层，使用 `paraphrase-multilingual-MiniLM-L12-v2` 多语言 Embedding 模型：

- 自动从对话中提取关键信息（用户偏好、事实、决策等）
- 下次对话时检索相关记忆，提供上下文感知的回复
- 按用户隔离，支持语义搜索（中英文）

模型路径可通过环境变量 `EMBEDDING_MODEL` 配置，默认 `/home/ubuntu/modelscope/paraphrase-multilingual-MiniLM-L12-v2`。

### 记忆管理命令

在飞书中发送以下命令管理记忆功能（按用户隔离，互不影响）：

| 命令 | 功能 |
|------|------|
| `/memory status` | 查看记忆开关状态和已存储的记忆条数 |
| `/memory on` | 开启记忆（默认状态） |
| `/memory off` | 关闭记忆 — 不再存储、检索、提取记忆，prompt 原文直传 Kiro |
| `/memory clear` | 清除所有历史记忆数据 |
| `/memory` | 显示帮助信息 |

> **提示**：当记忆内容干扰 Kiro skill 触发时（如 RDS 事件检查等），可先 `/memory off` 关闭记忆再发送指令。

```bash
# 运行记忆层测试
python3 test_memory.py
```

## 查看日志

```bash
sudo journalctl -u feishu-kiro-bot -f
```

## 使用 Skills（自定义 Agent）

Kiro CLI 支持通过 **skills** 扩展能力（如 AWS 巡检、文档生成、成本分析等）。要让 Bot 使用 skills，需要：

### 1. 创建自定义 Agent 配置

在 `~/.kiro/agents/` 目录下创建 JSON 文件，例如 `my-dev-bot.json`：

```json
{
    "name": "my-dev-bot",
    "description": "Simple bot for development purposes",
    "resources": [
        "skill://.kiro/skills/**/SKILL.md"
    ],
    "tools": ["*"]
}
```

字段说明：

| 字段 | 说明 |
|------|------|
| `name` | Agent 名称，用于 `KIRO_AGENT` 引用 |
| `description` | Agent 描述 |
| `resources` | 引用的 skills，`skill://.kiro/skills/**/SKILL.md` 表示加载所有 skills |
| `tools` | 允许使用的工具，`["*"]` 表示全部 |

Skills 文件放在 `~/.kiro/skills/<skill-name>/SKILL.md`，每个 SKILL.md 定义一个技能的触发条件和执行逻辑。

### 2. 在 .env 中指定 Agent

```bash
KIRO_AGENT=my-dev-bot
```

### 3. 重启服务

```bash
sudo systemctl restart feishu-kiro-bot
```

Bot 启动后会使用 `--agent my-dev-bot` 参数调用 kiro-cli，自动加载该 Agent 配置的所有 skills。

## 多轮对话与会话管理

Bot 支持跨消息的上下文保持。30 分钟内的连续消息自动延续同一个 Kiro 会话，无需额外操作。

### 会话管理命令

| 命令 | 功能 |
|------|------|
| `/new` | 强制开启新会话（不再延续之前的上下文） |
| `/resume <编号>` | 恢复指定历史会话 |
| `/sessions` | 列出你的历史会话 |
| `/status` | 查看当前后台任务状态 |
| `/cancel` | 取消当前后台任务 |

### 异步执行

对于复杂任务（如生成报告、多步骤分析），如果 120 秒内未完成，Bot 会自动转入后台处理并在完成后主动推送结果。

## 测试场景

部署完成后，按以下场景验证功能是否正常。

### 场景 1：基础命令

| 步骤 | 发送 | 预期结果 |
|------|------|---------|
| 1 | `/sessions` | 📭 你还没有历史会话 |
| 2 | `/status` | 没有正在运行的后台任务。 |
| 3 | `/cancel` | 没有正在运行的后台任务 |

### 场景 2：新建会话 + 自动 resume（上下文保持）

| 步骤 | 发送 | 预期结果 |
|------|------|---------|
| 1 | `今天是几月几号？` | 回复日期，末尾带 `📎 会话 #1 ...` |
| 2 | `那再加三天呢` | 回复正确日期（理解上下文），仍是 `📎 会话 #1` |
| 3 | `/sessions` | 显示 `#1 \| 刚刚 \| 今天是几月几号？ (2条)` |

### 场景 3：/new 强制新建

| 步骤 | 发送 | 预期结果 |
|------|------|---------|
| 1 | `/new` | 🆕 已切换到新会话模式 |
| 2 | `1+1等于几` | 回复结果，末尾带 `📎 会话 #2`（新编号） |
| 3 | `/sessions` | 显示 #1 和 #2 两个会话 |

### 场景 4：/resume 恢复历史会话

| 步骤 | 发送 | 预期结果 |
|------|------|---------|
| 1 | `/resume 1` | 🔄 已恢复会话 #1 ... |
| 2 | `之前算到多少了` | Kiro 能回忆起会话 #1 的上下文 |

### 场景 5：异步执行（复杂任务）

| 步骤 | 发送 | 预期结果 |
|------|------|---------|
| 1 | `/new` | 新建会话 |
| 2 | `检查cn-northwest-1 EC2 实例状态` | 120s 内完成则直接返回；超时则收到"⏳ 任务较复杂，已转入后台处理" |
| 3 | `/status`（等待期间） | 显示后台任务运行时间和指令 |

### 场景 6：错误处理

| 步骤 | 发送 | 预期结果 |
|------|------|---------|
| 1 | `/resume 999` | ❌ 未找到会话 #999 |
| 2 | `/resume` | 用法提示 |
| 3 | `/resume abc` | ❌ 请输入数字编号 |

## 常见问题

**Q: 连接不上飞书？**
A: 确认事件订阅已切换为「使用长连接接收事件」，且应用已发布。

**Q: 机器人不回复？**
A: 检查日志，确认 kiro-cli 可正常运行：`kiro-cli chat --prompt "hello"`

**Q: 断线会自动重连吗？**
A: 会。飞书 SDK 内置自动重连机制。
