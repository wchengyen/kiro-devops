# Model 选择功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 kiro-devops 中引入全局 Model 配置，支持通过 `setup.sh` 初次设定和 Dashboard Config 页面运行时修改，分别控制用户聊天和后台任务的 kiro-cli `--model` 参数。

**Architecture:** 两个环境变量 `DEFAULT_MODEL`（聊天）和 `BACKGROUND_MODEL`（后台/告警）作为唯一配置源，Dashboard 通过调用 `kiro-cli --list-models` 动态获取可选列表，三处 kiro-cli 调用点按需注入 `--model`。

**Tech Stack:** Python 3, Flask, Vue 3 (CDN), bash, pytest

---

## File Structure

| 文件 | 职责 |
|---|---|
| `.env.example` | 新增 model 配置示例注释 |
| `dashboard/config_store.py` | `CORE_KEYS` 追加 `DEFAULT_MODEL`、`BACKGROUND_MODEL` |
| `dashboard/api.py` | 新增 `GET /models` 路由，调用 `kiro-cli --list-models` |
| `dashboard/static/app.js` | Core Config tab 新增两个 model 下拉框（支持降级为文本输入） |
| `kiro_executor.py` | `KiroExecutor.execute()` 注入 `DEFAULT_MODEL` |
| `message_handler.py` | `_call_kiro_simple()` 注入 `BACKGROUND_MODEL` |
| `webhook_server.py` | `_trigger_analysis()` 注入 `BACKGROUND_MODEL` |
| `setup.sh` | `setup_kiro()` 增加交互式 model 选择 |
| `tests/test_config_store.py` | 补充 model 环境变量读写测试 |
| `tests/test_dashboard_api.py` | 补充 `/models` 路由测试 |

---

### Task 1: 配置层 — 更新 CORE_KEYS

**Files:**
- Modify: `dashboard/config_store.py:9-18`
- Test: `tests/test_config_store.py`

- [ ] **Step 1: 修改 CORE_KEYS**

```python
CORE_KEYS = [
    "KIRO_AGENT",
    "ALERT_NOTIFY_USER_ID",
    "ALERT_AUTO_ANALYZE_SEVERITY",
    "WEBHOOK_TOKEN",
    "WEBHOOK_PORT",
    "WEBHOOK_HOST",
    "ENABLE_MEMORY",
    "GROUP_AT_ONLY",
    "DEFAULT_MODEL",
    "BACKGROUND_MODEL",
]
```

- [ ] **Step 2: 写测试 — 验证 model 字段可被读写**

在 `tests/test_config_store.py` 末尾追加：

```python
def test_read_core_config_includes_model_keys(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEFAULT_MODEL=deepseek-3.2\nBACKGROUND_MODEL=qwen3-coder-next\n"
    )
    store = ConfigStore(env_path=str(env_file))
    cfg = store.read_core_config()
    assert cfg["DEFAULT_MODEL"] == "deepseek-3.2"
    assert cfg["BACKGROUND_MODEL"] == "qwen3-coder-next"


def test_write_core_config_persists_model_keys(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("KIRO_AGENT=my-agent\n")
    store = ConfigStore(env_path=str(env_file))
    store.write_core_config({"DEFAULT_MODEL": "glm-5", "BACKGROUND_MODEL": ""})
    content = env_file.read_text()
    assert "DEFAULT_MODEL=glm-5" in content
    assert "BACKGROUND_MODEL=" in content
    assert "KIRO_AGENT=my-agent" in content
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/test_config_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 4: Commit**

```bash
git add dashboard/config_store.py tests/test_config_store.py
git commit -m "feat(config): add DEFAULT_MODEL and BACKGROUND_MODEL to CORE_KEYS"
```

---

### Task 2: Dashboard API — 新增 `/models` 路由

**Files:**
- Modify: `dashboard/api.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: 查看现有 `/config` 路由位置，确认插入点**

在 `dashboard/api.py` 中搜索 `@dashboard_bp.route("/config"` 的位置，在附近插入 `/models` 路由。

- [ ] **Step 2: 实现 `/models` 路由**

在 `dashboard/api.py` 的 `read_alert_defaults` 函数之后（约第 148 行附近）插入：

```python
@dashboard_bp.route("/models", methods=["GET"])
@require_auth
def list_models():
    """Return available models from kiro-cli --list-models."""
    import json as _json
    import shutil
    import subprocess

    kiro_bin = shutil.which("kiro-cli") or "/home/ubuntu/.local/bin/kiro-cli"
    try:
        result = subprocess.run(
            [kiro_bin, "chat", "--list-models", "--format", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            data = _json.loads(result.stdout)
            return jsonify(data)
    except Exception as e:
        return jsonify({"models": [], "default_model": None, "error": str(e)}), 500

    return jsonify({"models": [], "default_model": None, "error": "kiro-cli failed"}), 500
```

- [ ] **Step 3: 写测试 — 验证 `/models` 路由**

在 `tests/test_dashboard_api.py` 末尾追加：

```python
def test_get_models(auth_client, monkeypatch):
    """Test /models returns structure even if kiro-cli is mocked."""
    import subprocess

    def mock_run(*args, **kwargs):
        class R:
            returncode = 0
            stdout = '{"models": [{"model_id": "test-model"}], "default_model": "test-model"}'
        return R()

    monkeypatch.setattr(subprocess, "run", mock_run)
    resp = auth_client.get("/api/dashboard/models")
    assert resp.status_code == 200
    data = resp.json
    assert "models" in data
    assert data["default_model"] == "test-model"


def test_get_models_fallback_on_error(auth_client, monkeypatch):
    """Test /models returns empty list when kiro-cli fails."""
    import subprocess

    def mock_run(*args, **kwargs):
        class R:
            returncode = 1
            stdout = ""
        return R()

    monkeypatch.setattr(subprocess, "run", mock_run)
    resp = auth_client.get("/api/dashboard/models")
    assert resp.status_code in (200, 500)
    data = resp.json
    assert "models" in data
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/test_dashboard_api.py -v`
Expected: PASS（包括新增的两个测试）

- [ ] **Step 5: Commit**

```bash
git add dashboard/api.py tests/test_dashboard_api.py
git commit -m "feat(dashboard): add /models API to list available kiro-cli models"
```

---

### Task 3: Dashboard UI — Core Config 增加 Model 下拉框

**Files:**
- Modify: `dashboard/static/app.js`

- [ ] **Step 1: 定位 Core Config 模板**

在 `dashboard/static/app.js` 中找到 `ConfigPage` 组件的 `core` tab 模板（约第 1135-1160 行），定位 `KIRO_AGENT` 输入框的位置。

- [ ] **Step 2: 修改模板 — 在 KIRO_AGENT 后追加两个 model 字段**

在 `KIRO_AGENT` 的 `<label>...</label>` 块之后插入：

```html
      <label>
        默认聊天模型 (DEFAULT_MODEL)
        <select v-if="modelOptions.length" v-model="core.DEFAULT_MODEL">
          <option value="">系统默认 — kiro-cli 自动选择</option>
          <option v-for="m in modelOptions" :key="m.model_id" :value="m.model_id">
            {{ m.model_id }} — {{ m.description }}
          </option>
        </select>
        <input v-else v-model="core.DEFAULT_MODEL" placeholder="手动输入 model_id 或留空" />
      </label>
      <label>
        后台任务模型 (BACKGROUND_MODEL)
        <select v-if="modelOptions.length" v-model="core.BACKGROUND_MODEL">
          <option value="">系统默认 — kiro-cli 自动选择</option>
          <option v-for="m in modelOptions" :key="m.model_id" :value="m.model_id">
            {{ m.model_id }} — {{ m.description }}
          </option>
        </select>
        <input v-else v-model="core.BACKGROUND_MODEL" placeholder="手动输入 model_id 或留空" />
      </label>
```

- [ ] **Step 3: 修改 setup 逻辑 — 新增 model 数据和加载**

在 `ConfigPage` 的 `setup()` 函数中，找到 `core` 响应式对象的定义，确保包含默认值：

```javascript
    const core = reactive({
      KIRO_AGENT: "",
      ALERT_NOTIFY_USER_ID: "",
      ALERT_AUTO_ANALYZE_SEVERITY: "",
      WEBHOOK_TOKEN: "",
      WEBHOOK_PORT: "",
      WEBHOOK_HOST: "",
      ENABLE_MEMORY: "",
      GROUP_AT_ONLY: "",
      DEFAULT_MODEL: "",
      BACKGROUND_MODEL: "",
    });
```

在 `setup()` 中新增：

```javascript
    const modelOptions = ref([]);
    const modelError = ref("");

    async function loadModels() {
      try {
        const data = await api("/models");
        modelOptions.value = data.models || [];
        modelError.value = data.error || "";
      } catch (e) {
        modelOptions.value = [];
        modelError.value = String(e);
      }
    }
```

在 `load()` 函数中（读取 core config 之后），调用 `loadModels()`：

```javascript
    async function load() {
      try {
        const c = await api("/config");
        Object.assign(core, c.config || {});
      } catch {}
      await loadModels();
      // ... 后续已有代码 ...
    }
```

在 `return` 对象中暴露 `modelOptions`：

```javascript
    return {
      tab, core, mappings, serviceRules,
      modelOptions, modelError,  // 新增
      // ... 其他已有变量 ...
    };
```

- [ ] **Step 4: 手动验证**

启动 gateway：`source .env && python3 gateway.py`
浏览器访问 Dashboard Config 页面，确认：
- Core Config tab 出现两个 model 下拉框
- 下拉框第一项是 "系统默认"
- 选择 model 后点击保存，刷新页面后值被保留

- [ ] **Step 5: Commit**

```bash
git add dashboard/static/app.js
git commit -m "feat(dashboard): add DEFAULT_MODEL and BACKGROUND_MODEL selectors in Config page"
```

---

### Task 4: 执行层 — kiro_executor 注入 DEFAULT_MODEL

**Files:**
- Modify: `kiro_executor.py`
- Test: `tests/test_kiro_executor.py`（如存在）或手动验证

- [ ] **Step 1: 修改 `KiroExecutor.__init__` 和 `execute`**

```python
class KiroExecutor:
    def __init__(self, agent: str = ""):
        self._agent = agent
        self._default_model = os.environ.get("DEFAULT_MODEL", "").strip()
        self._running: dict[str, dict] = {}
        self._lock = threading.Lock()
```

在 `execute()` 中，找到 `cmd` 构建部分（约第 93-98 行），在 `self._agent` 条件之后、prompt 之前插入：

```python
        cmd = [kiro_bin, "chat", "--no-interactive", "-a", "--trust-tools=execute_bash", "--wrap", "never"]
        if session_id:
            cmd.append("--resume")
        if self._agent:
            cmd += ["--agent", self._agent]
        if self._default_model:
            cmd += ["--model", self._default_model]
        cmd.append(prompt)
```

- [ ] **Step 2: 手动验证**

设置环境变量：`export DEFAULT_MODEL=deepseek-3.2`
启动 gateway，发送一条消息，检查日志中的完整命令是否包含 `--model deepseek-3.2`：

```bash
# 在另一个终端
tail -f gateway.log | grep "完整命令"
```

取消环境变量后再次测试，确认不包含 `--model`。

- [ ] **Step 3: Commit**

```bash
git add kiro_executor.py
git commit -m "feat(executor): inject --model from DEFAULT_MODEL env in chat execution"
```

---

### Task 5: 执行层 — message_handler 注入 BACKGROUND_MODEL

**Files:**
- Modify: `message_handler.py`

- [ ] **Step 1: 修改 `_call_kiro_simple`**

在 `_call_kiro_simple()` 中（约第 62-79 行），找到 `cmd` 构建部分，修改为：

```python
    def _call_kiro_simple(self, prompt: str) -> str:
        """简单调用（供定时任务使用）."""
        log.info(f"调用 kiro-cli (simple): {prompt[:80]}...")
        try:
            cmd = [kiro_bin, "chat", "--no-interactive", "-a", "--wrap", "never"]
            if KIRO_AGENT:
                cmd += ["--agent", KIRO_AGENT]
            bg_model = os.environ.get("BACKGROUND_MODEL", "").strip()
            if bg_model:
                cmd += ["--model", bg_model]
            cmd.append(prompt)
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=KIRO_TIMEOUT,
                cwd=os.path.expanduser("~"), env={**os.environ, "NO_COLOR": "1"},
            )
            output = result.stdout.strip() or result.stderr.strip() or "Kiro 未返回结果"
            return output
        except subprocess.TimeoutExpired:
            return f"⏰ Kiro 处理超时（{KIRO_TIMEOUT}s）"
        except Exception as e:
            return f"❌ Kiro 调用失败: {e}"
```

- [ ] **Step 2: 手动验证**

设置 `export BACKGROUND_MODEL=qwen3-coder-next`，创建一个 `/schedule` 定时任务，检查日志中命令是否包含 `--model`。

- [ ] **Step 3: Commit**

```bash
git add message_handler.py
git commit -m "feat(handler): inject --model from BACKGROUND_MODEL env in scheduled tasks"
```

---

### Task 6: 执行层 — webhook_server 注入 BACKGROUND_MODEL

**Files:**
- Modify: `webhook_server.py`

- [ ] **Step 1: 修改 `_trigger_analysis`**

在 `_trigger_analysis()` 中（约第 209-213 行），找到 `cmd` 构建部分，修改为：

```python
    cmd = [kiro_bin, "chat", "--no-interactive", "-a", "--wrap", "never"]
    for tool in tools:
        cmd.append(f"--trust-tools={tool}")
    cmd += ["--agent", agent]
    bg_model = os.environ.get("BACKGROUND_MODEL", "").strip()
    if bg_model:
        cmd += ["--model", bg_model]
    cmd.append(alert_payload)
```

- [ ] **Step 2: 手动验证**

设置 `export BACKGROUND_MODEL=qwen3-coder-next`，触发一条 webhook 告警，检查日志。

- [ ] **Step 3: Commit**

```bash
git add webhook_server.py
git commit -m "feat(webhook): inject --model from BACKGROUND_MODEL env in alert analysis"
```

---

### Task 7: setup.sh — 增加交互式 Model 选择

**Files:**
- Modify: `setup.sh`

- [ ] **Step 1: 在 `setup_kiro()` 中追加 model 选择逻辑**

在 `setup_kiro()` 函数末尾（`success "Kiro CLI 配置完成"` 之前）插入：

```bash
    # ----- Model 选择 -----
    local models_json
    models_json=$(kiro-cli chat --list-models --format json 2>/dev/null || echo "")

    if [ -n "$models_json" ]; then
        local model_list default_id
        model_list=$(echo "$models_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for m in d.get('models', []):
    print(m['model_id'])
")
        default_id=$(echo "$models_json" | python3 -c "import sys, json; print(json.load(sys.stdin).get('default_model', ''))")

        echo ""
        echo "可用模型列表："
        echo "  0) 系统默认 (${default_id})"
        local idx=1
        local map=""
        while IFS= read -r mid; do
            echo "  ${idx}) ${mid}"
            map="${map}${idx}:${mid}\n"
            idx=$((idx + 1))
        done <<< "$model_list"

        # DEFAULT_MODEL
        local current_default choice selected
        current_default=$(get_env_var "DEFAULT_MODEL" "")
        read -p "选择默认聊天模型 [当前: ${current_default:-系统默认}]: " choice
        if [ -z "$choice" ]; then
            : # 保留当前值
        elif [ "$choice" = "0" ]; then
            update_env_var "DEFAULT_MODEL" ""
        else
            selected=$(echo -e "$map" | grep "^${choice}:" | cut -d: -f2)
            if [ -n "$selected" ]; then
                update_env_var "DEFAULT_MODEL" "$selected"
            else
                warn "无效选项，保留当前值"
            fi
        fi

        # BACKGROUND_MODEL
        local current_bg
        current_bg=$(get_env_var "BACKGROUND_MODEL" "")
        read -p "选择后台任务模型 [当前: ${current_bg:-系统默认}]: " choice
        if [ -z "$choice" ]; then
            :
        elif [ "$choice" = "0" ]; then
            update_env_var "BACKGROUND_MODEL" ""
        else
            selected=$(echo -e "$map" | grep "^${choice}:" | cut -d: -f2)
            if [ -n "$selected" ]; then
                update_env_var "BACKGROUND_MODEL" "$selected"
            else
                warn "无效选项，保留当前值"
            fi
        fi
    else
        warn "无法获取模型列表，kiro-cli 可能未安装或网络不可用"
        read -p "手动输入默认聊天模型（留空使用系统默认）: " default_model
        [ -n "$default_model" ] && update_env_var "DEFAULT_MODEL" "$default_model"
        read -p "手动输入后台任务模型（留空使用系统默认）: " bg_model
        [ -n "$bg_model" ] && update_env_var "BACKGROUND_MODEL" "$bg_model"
    fi
```

- [ ] **Step 2: 手动验证**

运行 `./setup.sh`，选择模式 `4`（仅配置通用项），验证：
- 正确显示可用模型列表（带编号）
- 选择 `0` 会写入空值
- 选择有效编号会写入对应 model_id
- 回车保留当前值
- 如果 kiro-cli 不可用，回退到手动输入

- [ ] **Step 3: Commit**

```bash
git add setup.sh
git commit -m "feat(setup): add interactive model selection in setup_kiro"
```

---

### Task 8: 更新 .env.example

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: 在 KIRO_AGENT 注释后追加 model 注释**

```bash
# 指定 Kiro agent（可选，留空则使用默认 agent）
# KIRO_AGENT=my-dev-bot

# 默认聊天模型（可选，留空使用 kiro-cli 默认）
# DEFAULT_MODEL=
# 后台任务模型（Scheduler / 告警分析，可选，留空使用 kiro-cli 默认）
# BACKGROUND_MODEL=
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs(env): add DEFAULT_MODEL and BACKGROUND_MODEL examples"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- [x] `.env.example` 注释 → Task 8
- [x] `config_store.py` CORE_KEYS → Task 1
- [x] Dashboard `/models` API → Task 2
- [x] Dashboard UI 下拉框 → Task 3
- [x] `kiro_executor.py` 注入 `--model` → Task 4
- [x] `message_handler.py` 注入 `--model` → Task 5
- [x] `webhook_server.py` 注入 `--model` → Task 6
- [x] `setup.sh` 交互式选择 → Task 7

**2. Placeholder scan:**
- [x] 无 TBD/TODO/"implement later"
- [x] 每个代码步骤含完整代码
- [x] 每个测试步骤含完整测试代码
- [x] 命令和预期输出明确

**3. Type consistency:**
- [x] 环境变量名称统一：`DEFAULT_MODEL`、`BACKGROUND_MODEL`
- [x] API 路径统一：`/models`
- [x] 属性名统一：`model_id`、`description`、`default_model`

---

## Post-Implementation Verification

所有任务完成后，运行以下验证：

```bash
# 1. 单元测试
pytest tests/test_config_store.py tests/test_dashboard_api.py -v

# 2. 启动 gateway，验证 Dashboard 下拉框正常渲染
source .env && python3 gateway.py
# 浏览器访问 http://localhost:8080/dashboard/#/config

# 3. 验证 kiro-cli 调用包含 --model
tail -f gateway.log | grep "完整命令"
# 发送消息，确认日志中出现 --model <value>
```
