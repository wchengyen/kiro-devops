# 记忆系统架构改造计划

> 备份分支：`backup/before-memory-rewrite` (b41494f)
> 回退命令：`git reset --hard backup/before-memory-rewrite`

---

## 改造目标

1. **双层记忆**：Semantic Memory（用户偏好/事实）+ Episodic Memory（系统事件/变更/告警）
2. **存储去重**：事件记忆使用 SQLite（结构化、可审计、零额外依赖）；语义记忆迁移到 SQLite FTS5（去掉本地向量模型）
3. **Prompt 分离**：Semantic → 注入 system prompt 影响行为；Episodic → 按需检索，作为 RAG 参考附加到 user prompt，不影响模型行为
4. **事件准入**：显式准入（外部系统推送 / 用户手动录入 / Agent 识别后确认），禁止自动提取事件

---

## 分步改造计划

### Step 1：新建 events.db SQLite 存储层 + EventStore 类

**范围**：纯新增文件，不改动现有业务代码

**交付物**：
- `event_store.py` —— EventStore 类
  - `add_event(user_id, event_type, entities, title, description, ts, source, severity)`
  - `search_events(user_id, query, entities=None, event_types=None, days=14, top_k=10)`
  - `list_events(user_id, days=30, event_types=None)`
  - `get_event_by_id(event_id)`
  - `clear(user_id=None)`
- SQLite Schema：
  - `events` 表（主表，结构化字段）
  - `events_fts` 虚拟表（FTS5 全文索引 title + description）
  - 索引：`idx_user_time`, `idx_event_type`
- `test_event_store.py` —— 单元测试

**验收标准**：
- `python test_event_store.py` 全部通过
- 1000 条事件插入耗时 < 3 秒
- 带时间过滤的查询耗时 < 50ms
- 单文件 `events.db` 可独立复制/备份

---

### Step 2：Semantic Memory 迁移到 SQLite FTS5

**范围**：替换 `memory.py` 内部实现，保留对外接口不变

**交付物**：
- `semantic_store.py` —— SemanticStore 类（SQLite + FTS5）
  - `add(user_id, text)`
  - `search(user_id, query, top_k=5)` —— FTS5 MATCH + 简单分词重叠排序
  - `list_all(user_id)`
  - `clear(user_id=None)`
- 改造 `memory.py` —— 保留类名 `MemoryLayer`，内部委托给 SemanticStore
- 可选：移除 `sentence-transformers` 和 `chromadb` 依赖（或保留但默认不加载）

**验收标准**：
- `python test_memory.py` 仍全部通过（接口兼容）
- 进程启动不再加载 embedding 模型（启动时间从数秒降到毫秒）
- 语义搜索召回率与改造前持平（测试集验证）

---

### Step 3：Prompt 注入策略分离

**范围**：修改 `app.py` 的消息处理链路

**交付物**：
- 新增 `build_prompt(user_text, semantic_memories, episodic_memories=None)` 函数
- 规则：
  - `semantic_memories` → 拼接到 prompt 开头（"关于该用户的已知信息：..."）
  - `episodic_memories` → 拼接到 user_text 后，用围栏标注：
    ```
    用户消息：{user_text}

    --- 以下历史事件仅供参考，不影响你的判断 ---
    [事件列表]
    -----------------------------------------------
    ```
- 修改 `handle_user_message` 调用逻辑：
  - semantic search 仍走现有逻辑
  - 新增：如果用户消息包含已知实体（或 search_events 返回结果），则附加 episodic

**验收标准**：
- 通过日志/测试可验证：episodic 内容出现在 user prompt 区域，不在 system prompt
- 用户闲聊（无实体命中）时，不触发事件检索，不增加 token

---

### Step 4：用户命令与外部事件录入接口

**范围**：新增交互与接入能力

**交付物**：
- `/event` 命令解析（`app.py` 中 `handle_user_message`）
  - 用法：`/event 类型=应用发版 实体=订单服务,test1 标题=... 描述=...`
  - 成功返回："✅ 已记录事件 #42：..."
- 外部推送模块 `event_ingest.py`
  - `webhook_handler(payload)` —— 校验 + 入库
  - 支持来源标识：jenkins / zabbix / apollo / manual
  - 幂等校验：基于 `id` 字段去重
- 轻量实体提取 `entity_extractor.py`
  - 规则：从 title/description 中匹配已知的资源名正则（如 `[a-z0-9-]+数据库`、`服务[a-z0-9-]+`）
  - 无需 LLM，纯本地逻辑

**验收标准**：
- 发送 `/event` 后事件入库，可查询
- 重复推送相同 id 不重复入库
- 实体提取准确率 > 80%（用测试集验证）

---

### Step 5：集成到消息处理链路

**范围**：把 Step 1~4 的模块串联到 `app.py`

**交付物**：
- `app.py` 中集成 EventStore 实例
- 用户消息处理时自动决策是否检索事件：
  - 先对用户消息做轻量实体提取
  - 如果提取到实体 → 调用 `event_store.search_events(entities=..., days=14)`
  - 如果返回非空 → 附加到 prompt
- `/memory` 命令扩展：
  - `/memory events` —— 列出最近事件
  - `/memory events clear` —— 清空个人事件（保留语义记忆）

**验收标准**：
- 端到端测试：模拟用户问"test1 数据库怎么了"，回复中包含相关历史事件
- 模拟用户问"你好"，不触发事件检索，回复正常

---

### Step 6：端到端测试、性能基准与回退验证

**范围**：全链路验证与文档

**交付物**：
- `test_integration.py` —— 端到端测试
- `test_performance.py` —— 性能基准（事件插入、查询延迟、内存占用）
- 回退验证脚本：确认 `git reset --hard backup/before-memory-rewrite` 后系统可恢复
- 更新 README：新记忆架构说明、/event 用法、webhook 接入文档

**验收标准**：
- 全部测试通过
- 事件查询 p95 延迟 < 100ms（10000 条数据量）
- 进程内存占用 < 100MB（不含 kiro-cli）
- 回退后 `python app.py` 仍能正常启动（旧代码兼容）

---

## 测试Overview

| 步骤 | 测试文件 | 验证重点 |
|------|---------|---------|
| Step 1 | `test_event_store.py` | CRUD、时间过滤、实体过滤、FTS5 全文、空库边界 |
| Step 2 | `test_semantic_store.py` | 接口兼容、召回率、启动速度 |
| Step 3 | `test_prompt_builder.py` | Prompt 结构、围栏标注、无事件时不污染 |
| Step 4 | `test_event_ingest.py` | /event 解析、webhook 幂等、实体提取 |
| Step 5 | `test_integration.py` | 端到端消息链路、自动事件检索触发 |
| Step 6 | `test_performance.py` + 回退脚本 | 性能、回退可靠性 |

---

## 依赖变化

| 依赖 | 改造前 | 改造后 | 说明 |
|------|--------|--------|------|
| chromadb | ✅ 必需 | ❌ 移除 | 语义记忆改用 SQLite |
| sentence-transformers | ✅ 必需 | ❌ 移除 | 本地 embedding 模型不再需要 |
| sqlite3 | ✅ 内置 | ✅ 内置 | 零新增依赖 |

---

## 风险与应对

| 风险 | 应对方案 |
|------|---------|
| Semantic 搜索质量下降（去掉向量） | Step 2 中保留旧 memory.py 为 `memory_legacy.py`，可一键切换回退 |
| 实体提取规则覆盖不全 | 初始规则覆盖常见资源名模式，后续可扩展；不阻断核心流程 |
| 事件量暴增导致 SQLite 性能下降 | 设计时已考虑索引；若超 10 万条，可再引入按月份分区 |
