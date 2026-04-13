# 飞书 ↔ Kiro CLI 桥接服务（WebSocket 长连接版）

在飞书中 @机器人 发消息，自动调用 Kiro CLI 处理并回复结果。

**无需公网 IP、无需端口开放、无需 nginx 反向代理。**

## 架构

```
飞书用户 @Bot "分析一下成本"
       ↓
飞书云 ←—WebSocket 长连接—→ 本服务 (Python)
                                ↓ subprocess
                           kiro-cli chat --prompt "分析一下成本"
                                ↓
                           本服务收到结果
                                ↓ 飞书 SDK reply
                           飞书用户收到回复
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
2. 「权限管理」→ 开通：
   - `im:message` — 获取与发送消息
   - `im:message:send_as_bot` — 以应用身份发送消息

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
pip3 install lark-oapi chromadb sentence-transformers
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

## 记忆功能

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

## 常见问题

**Q: 连接不上飞书？**
A: 确认事件订阅已切换为「使用长连接接收事件」，且应用已发布。

**Q: 机器人不回复？**
A: 检查日志，确认 kiro-cli 可正常运行：`kiro-cli chat --prompt "hello"`

**Q: 断线会自动重连吗？**
A: 会。飞书 SDK 内置自动重连机制。
