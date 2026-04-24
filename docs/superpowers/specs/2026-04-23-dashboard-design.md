# feishu-kiro-bot Web Dashboard 设计文档

**日期**: 2026-04-23  
**状态**: Draft → Approved  
**作者**: AI Assistant  

---

## 1. 背景与目标

### 1.1 背景

feishu-kiro-bot 当前是一个纯命令行/消息交互的飞书 Bot，所有运维信息（事件、定时任务、Agent/Skill 状态）分散在：
- `events.db`（SQLite）——事件记录
- `scheduled_jobs.json` ——定时任务
- `~/.kiro/agents/` 和 `~/.kiro/skills/` ——Kiro 生态
- `.env` ——运行配置

管理员需要通过 `sqlite3` 命令行或 Feishu `/schedule list`、`/memory events` 等命令查看，缺乏一个集中式的可视化面板。

### 1.2 目标

在现有 Bot 架构上叠加一个轻量 Web Dashboard，实现：
1. **Kiro Agent/Skill 只读展示** ——让管理员一目了然当前可用的 Kiro 能力
2. **Bot 配置可视化编辑** ——无需 SSH 修改 `.env` 即可调整核心参数和告警映射
3. **定时任务 Web 端 CRUD** ——替代 `/schedule` 命令，降低操作门槛
4. **事件 Web 端 CRUD** ——替代 `/event` 命令和手动 SQL 查询，支持搜索过滤
5. **零额外依赖、零构建步骤** ——延续 Bot 的轻量哲学

### 1.3 非目标

- 不替代 Feishu 聊天交互（Dashboard 是补充，不是替代）
- 不引入 npm/webpack/Node.js 等前端构建工具
- 不引入新的数据库（复用现有 SQLite + JSON）
- 不修改现有 Webhook `/event` 接口的行为

---

## 2. 设计原则

| 原则 | 说明 |
|------|------|
| **零额外依赖** | 仅使用已安装的 `Flask` + `Vue 3 CDN`，不引入新 Python 包或前端构建工具 |
| **路径隔离** | Dashboard 路由挂载在 `/dashboard/` 和 `/api/dashboard/`，与现有 `/event`、`/health` 不冲突 |
| **同进程运行** | Dashboard 与 Bot WebSocket/Webhook 共用同一个 Python 进程，单 `systemd` 服务管理 |
| **复用现有存储** | 事件读写复用 `EventStore`，定时任务读写复用 `Scheduler`，配置读写复用 `.env` |
| **向后兼容** | 所有现有 API、命令、数据格式保持不变 |

---

## 3. 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                    feishu-kiro-bot 进程                      │
│  ┌─────────────────┐    ┌─────────────────────────────────┐ │
│  │  lark-oapi      │    │  Flask app (共用 8080 端口)      │ │
│  │  WebSocket      │    │                                 │ │
│  │  飞书消息接收    │    │  /event          ← Webhook     │ │
│  │                 │    │  /health         ← 健康检查     │ │
│  │  /schedule      │    │  /dashboard/     ← Web SPA 入口 │ │
│  │  /memory        │    │  /api/dashboard/ ← Dashboard API│ │
│  │  /new /resume   │    │       ├── /auth                 │ │
│  │  ...            │    │       ├── /agents               │ │
│  │                 │    │       ├── /skills               │ │
│  │                 │    │       ├── /config               │ │
│  │                 │    │       ├── /mappings             │ │
│  │                 │    │       ├── /events               │ │
│  │                 │    │       └── /scheduler            │ │
│  └─────────────────┘    └─────────────────────────────────┘ │
│           │                              │                  │
│           ▼                              ▼                  │
│    ┌─────────────┐              ┌─────────────────────┐    │
│    │ 飞书用户     │              │ 浏览器管理员         │    │
│    └─────────────┘              └─────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
    ┌──────────────┬──────────────┬──────────────┬──────────────┐
    │  events.db   │ scheduled_   │   .env       │ ~/.kiro/     │
    │  (SQLite)    │ jobs.json    │  (配置)       │ agents/      │
    │              │              │              │ skills/      │
    └──────────────┴──────────────┴──────────────┴──────────────┘
```

---

## 4. 模块结构

```
dashboard/                          # 新增 Python 包
├── __init__.py                     # Blueprint 注册、鉴权中间件、CORS
├── api.py                          # 所有 /api/dashboard/* 路由
├── config_store.py                 # .env 读写 + 映射配置 JSON 持久化
├── kiro_scanner.py                 # 扫描 ~/.kiro/agents/ 和 ~/.kiro/skills/
└── static/                         # 前端静态文件
    ├── index.html                  # Vue 3 CDN SPA 入口
    ├── app.js                      # Vue 路由、组件、API 调用
    └── style.css                   # 自定义样式（轻量，不引入大型 CSS 框架）
```

### 4.1 文件职责

| 文件 | 职责 |
|------|------|
| `dashboard/__init__.py` | 创建 Flask Blueprint `dashboard_bp`，注册静态文件路由，实现 `require_auth` 装饰器 |
| `dashboard/api.py` | 所有 API 端点实现，直接调用现有 `EventStore`、`Scheduler`、`config_store` |
| `dashboard/config_store.py` | 读写 `.env`（保留注释、只修改目标行），读写 `dashboard_config.json`（告警映射） |
| `dashboard/kiro_scanner.py` | `list_agents()` 扫描 `~/.kiro/agents/*.json`；`list_skills()` 扫描 `~/.kiro/skills/**/SKILL.md` |
| `dashboard/static/index.html` | HTML 骨架，引入 Vue 3 CDN、引入 app.js/style.css |
| `dashboard/static/app.js` | Vue Router 定义 5 个页面，axios/fetch 调用 API，组件逻辑 |
| `dashboard/static/style.css` | 轻量 CSS，约 200 行，覆盖侧边栏、卡片、表格、表单基础样式 |

---

## 5. 后端 API 设计

### 5.1 鉴权

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/dashboard/auth` | Body: `{token: string}`，校验 `.env` 中 `DASHBOARD_TOKEN`，成功设置 Cookie `dashboard_session` |
| POST | `/api/dashboard/logout` | 清除 Cookie |

### 5.2 Agent / Skill（只读）

| 方法 | 路径 | 返回 |
|------|------|------|
| GET | `/api/dashboard/agents` | `[{name, description, tools, resources}]` |
| GET | `/api/dashboard/skills` | `[{name, description, triggers, path}]` |

### 5.3 配置（可编辑）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dashboard/config` | 返回 Bot 核心配置：`KIRO_AGENT`, `ALERT_NOTIFY_USER_ID`, `ALERT_AUTO_ANALYZE_SEVERITY`, `WEBHOOK_TOKEN`（脱敏）, `WEBHOOK_PORT`, `ENABLE_MEMORY` |
| POST | `/api/dashboard/config` | 保存核心配置，写回 `.env`。`WEBHOOK_TOKEN` 修改需二次确认 |
| GET | `/api/dashboard/mappings` | 返回告警映射：`[{source, severity, event_type, agent}]` |
| POST | `/api/dashboard/mappings` | 保存告警映射，写入 `dashboard_config.json` |

### 5.4 事件（CRUD）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dashboard/events` | Query: `?source=&severity=&event_type=&q=&limit=50&offset=0`。支持 FTS5 关键词搜索 |
| POST | `/api/dashboard/events` | Body: `{id, event_type, title, description, entities, source, severity, user_id}`。复用 `webhook_handler()` + `ingest_to_store()` |
| DELETE | `/api/dashboard/events/<id>` | 删除单条事件 |

### 5.5 定时任务（CRUD）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dashboard/scheduler` | 返回所有任务列表 |
| POST | `/api/dashboard/scheduler` | 新增任务，Body: `{frequency, time_str, prompt, user_id}` |
| PUT | `/api/dashboard/scheduler/<id>` | 编辑任务或启停状态，Body: `{frequency, time_str, prompt, enabled}` |
| DELETE | `/api/dashboard/scheduler/<id>` | 删除任务 |

---

## 6. 前端设计

### 6.1 技术栈

- **Vue 3**（CDN: `https://unpkg.com/vue@3/dist/vue.global.js`）
- **Vue Router 4**（CDN: `https://unpkg.com/vue-router@4/dist/vue-router.global.js`）
- **原生 Fetch API**（不引入 axios，减少依赖）
- **轻量 CSS**（约 200 行自定义样式，不引入 Bootstrap/Element Plus）

### 6.2 页面结构

```
┌─────────────────────────────────────────┐
│  🔷 feishu-kiro-bot Dashboard    [退出] │  ← 顶部导航
├──────────┬──────────────────────────────┤
│          │                              │
│  📊 概览  │     内容区域（Router View）   │
│  🤖 Agents│                              │
│  📋 Skills│                              │
│  📝 事件  │                              │
│  ⏰ 任务  │                              │
│  ⚙️ 配置  │                              │
│          │                              │
└──────────┴──────────────────────────────┘
```

### 6.3 各页面内容

| 页面 | 路径 | 内容 |
|------|------|------|
| **概览** | `/dashboard/#/` | 4 个卡片：今日事件数、运行中任务数、Agent 数量、Skill 数量 |
| **Agents** | `/dashboard/#/agents` | 卡片网格，展示 name / description / tools |
| **Skills** | `/dashboard/#/skills` | 卡片网格，展示 name / description / triggers |
| **事件** | `/dashboard/#/events` | 表格（id / time / type / severity / source / title）+ 顶部搜索栏 + 筛选器 + 删除按钮 + "新建事件" 弹窗 |
| **任务** | `/dashboard/#/scheduler` | 表格（id / frequency / time / prompt / enabled）+ 启停开关 + 编辑弹窗 + 删除按钮 + "新建任务" 弹窗 |
| **配置** | `/dashboard/#/config` | 两个 Tab：①核心配置表单 ②告警映射表格（source + severity → agent） |

---

## 7. 鉴权设计

### 7.1 Token 来源

- `.env` 中新增 `DASHBOARD_TOKEN=<random_string>`（安装时由管理员设置）
- 与 `WEBHOOK_TOKEN` 分离，避免权限混淆

### 7.2 登录流程

1. 用户首次访问 `/dashboard/` → 未检测到 `dashboard_session` Cookie → 显示登录页
2. 用户输入 Token → POST `/api/dashboard/auth`
3. 后端校验 Token → 生成随机 `session_id` → 设置 `HttpOnly` Cookie `dashboard_session`
4. 前端跳转首页

### 7.3 API 保护

- 所有 `/api/dashboard/*` 路由（除 `/auth`）使用 `@require_auth` 装饰器
- 校验 Cookie 中的 `session_id` 是否存在于内存会话字典中
- 会话不过期（Bot 重启后需重新登录），或可选设置 24h 过期

### 7.4 安全边界

- Dashboard 默认监听与 Webhook 相同的 `WEBHOOK_HOST`（`127.0.0.1` 或 `0.0.0.0`）
- 如果暴露公网，强烈建议 `WEBHOOK_HOST=127.0.0.1` + Nginx 反向代理 + HTTPS
- `DASHBOARD_TOKEN` 与 `WEBHOOK_TOKEN` 强度要求一致

---

## 8. 数据库与数据流

### 8.1 数据流一致性

```
外部 Webhook ──POST /event─────────┐
                                   ├──► EventStore.add_event() ──► events.db
面板手动录入 ──POST /api/dashboard/events───┘

Feishu /schedule ───┐
                    ├──► Scheduler.handle_command() ──► scheduled_jobs.json
面板 Web CRUD ──────┘

SSH 编辑 .env ───┐
                 ├──► config_store.py 读写 ──► .env + dashboard_config.json
面板 Web 编辑 ────┘
```

### 8.2 配置持久化

| 配置类型 | 存储位置 | 说明 |
|---------|---------|------|
| 核心运行配置 | `.env` | `KIRO_AGENT`, `ALERT_NOTIFY_USER_ID` 等。修改后**需要重启服务生效**（文档明确提示） |
| 告警映射配置 | `dashboard_config.json` | 动态加载，**无需重启**。Bot 启动时读取，运行中可通过文件监听或定时重载 |

### 8.3 事件录入兼容性

- 面板录入的事件与 Webhook 录入的事件**数据模型完全一致**
- 面板录入时自动生成 `timestamp`（如未提供）
- 面板录入的 `id` 如留空，由后端生成 UUID
- 同样遵守 `INSERT OR IGNORE` 幂等语义

---

## 9. 兼容性保证

### 9.1 现有功能零影响

| 现有组件 | 保证 |
|---------|------|
| `POST /event` | 路由、逻辑、鉴权完全不变 |
| `GET /health` | 返回字段不变，可额外增加 `dashboard: true` |
| WebSocket 飞书消息 | 不受影响 |
| `/schedule` 命令 | 继续可用，与面板操作的数据源一致 |
| `/memory` 命令 | 不受影响 |
| `events.db` schema | 不新增、不修改、不删除任何表或字段 |
| `scheduled_jobs.json` schema | 不修改 |

### 9.2 部署兼容性

- 如果服务器未安装 Flask（极少数情况），Dashboard 自动静默禁用（`if Flask is None: pass`）
- 如果 `.env` 中未设置 `DASHBOARD_TOKEN`，Dashboard 路由注册但返回 `503 Dashboard not configured`
- 现有 `start.sh` 和 `systemd` 服务无需任何修改

---

## 10. 未来扩展

以下功能不在本次设计范围内，但架构预留了扩展能力：

| 功能 | 预留扩展点 |
|------|-----------|
| 语义记忆展示 | 新增 `/api/dashboard/semantic` 路由，读取 `memory_db/semantic_memory.db` |
| 用户会话管理 | 新增 `/api/dashboard/sessions` 路由，读取 `user_sessions.json` |
| 实时日志流 | 新增 `/api/dashboard/logs/stream` SSE 端点，读取 `journalctl` 或日志文件 |
| 图表可视化 | 前端引入 ECharts CDN，展示 CPU/内存趋势图（需后端提供聚合 API） |
| 多用户 RBAC | 扩展 `dashboard_session` 为带角色的会话对象 |
| 独立端口部署 | 将 Blueprint 拆分为独立 Flask app，监听新端口 |

---

## 11. 部署说明

### 11.1 环境变量（`.env` 新增）

```bash
# Dashboard 鉴权（必需）
DASHBOARD_TOKEN=change-me-strong-secret
```

### 11.2 访问方式

```
# 本地/内网访问
http://localhost:8080/dashboard/

# 通过 Nginx 反向代理暴露公网
https://bot.yourdomain.com/dashboard/
```

### 11.3 首次使用流程

1. 管理员设置 `.env` 中 `DASHBOARD_TOKEN`
2. 重启 `feishu-kiro-bot.service`
3. 浏览器访问 `http://<bot-ip>:8080/dashboard/`
4. 输入 `DASHBOARD_TOKEN` 登录
5. 在"配置"页查看和调整 Bot 参数

---

## 12. 风险评估

| 风险 | 缓解措施 |
|------|---------|
| `app.py` 过度膨胀 | 所有 Dashboard 代码隔离在 `dashboard/` 包中，`app.py` 仅 +3 行 import + register |
| 前端无构建导致维护困难 | Vue 3 CDN 单文件模式足够支撑 5 个简单页面；未来如复杂化再考虑 Vite |
| 配置修改后需重启 | 文档明确提示；`dashboard_config.json` 动态配置无需重启 |
| Token 泄露 | 建议 `WEBHOOK_HOST=127.0.0.1` + Nginx HTTPS 反向代理；生产环境定期轮换 Token |
| Cookie 会话丢失（Bot 重启） | 会话存内存，重启后需重新登录；可未来扩展为文件/Redis 持久化 |

---

## 附录：API 端点汇总

```
POST   /api/dashboard/auth              → 登录
POST   /api/dashboard/logout            → 登出

GET    /api/dashboard/agents            → Kiro Agent 列表
GET    /api/dashboard/skills            → Kiro Skill 列表

GET    /api/dashboard/config            → Bot 核心配置
POST   /api/dashboard/config            → 保存核心配置

GET    /api/dashboard/mappings          → 告警映射配置
POST   /api/dashboard/mappings          → 保存告警映射

GET    /api/dashboard/events            → 事件列表（支持过滤）
POST   /api/dashboard/events            → 手动录入事件
DELETE /api/dashboard/events/<id>       → 删除事件

GET    /api/dashboard/scheduler         → 定时任务列表
POST   /api/dashboard/scheduler         → 新增任务
PUT    /api/dashboard/scheduler/<id>    → 编辑/启停任务
DELETE /api/dashboard/scheduler/<id>    → 删除任务

GET    /dashboard/                      → SPA 入口（index.html）
GET    /dashboard/static/<path>         → 静态文件
```
