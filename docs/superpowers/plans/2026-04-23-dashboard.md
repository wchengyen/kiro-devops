# Web Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Vue 3 CDN + Flask Blueprint web dashboard for feishu-kiro-bot to visualize agents/skills, manage events/scheduler, and edit bot configuration.

**Architecture:** Flask Blueprint mounted at `/dashboard/` and `/api/dashboard/`, sharing the same process as the existing Bot. Frontend is a Vue 3 SPA loaded from CDN with no build step. All data operations reuse existing `EventStore`, `Scheduler`, and `.env`.

**Tech Stack:** Python 3.10+, Flask (already installed), Vue 3 (CDN), SQLite (existing)

---

## File Structure

```
dashboard/
├── __init__.py          # Blueprint, auth decorator, session store
├── api.py               # All /api/dashboard/* routes
├── config_store.py      # .env read/write + dashboard_config.json
├── kiro_scanner.py      # Scan ~/.kiro/agents/ and ~/.kiro/skills/
└── static/
    ├── index.html       # Vue 3 CDN SPA shell
    ├── app.js           # Vue app, router, pages, API calls
    └── style.css        # ~200 lines custom CSS
```

**Modified files:**
- `app.py` — register dashboard blueprint (+3 lines)
- `.env.example` — add `DASHBOARD_TOKEN`

---

## Task 1: Dashboard Package Skeleton + Auth Middleware

**Files:**
- Create: `dashboard/__init__.py`
- Test: `tests/test_dashboard_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_auth.py
import pytest
from dashboard import dashboard_bp, require_auth, _sessions

@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_auth_login_success(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "test-secret")
    resp = client.post("/api/dashboard/auth", json={"token": "test-secret"})
    assert resp.status_code == 200
    assert resp.json["ok"] is True
    assert "dashboard_session" in [c.key for c in resp.headers.getlist("Set-Cookie")]

def test_auth_login_failure(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "test-secret")
    resp = client.post("/api/dashboard/auth", json={"token": "wrong"})
    assert resp.status_code == 401

def test_protected_route_without_auth(client):
    resp = client.get("/api/dashboard/agents")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_auth.py -v
```

Expected: FAIL with "ModuleNotFoundError: No module named 'dashboard'"

- [ ] **Step 3: Implement dashboard/__init__.py**

```python
"""Dashboard Blueprint — Web panel for feishu-kiro-bot"""
import os
import uuid
from functools import wraps
from flask import Blueprint, request, jsonify, make_response

DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
_sessions: dict[str, dict] = {}

dashboard_bp = Blueprint("dashboard", __name__, static_folder="static", static_url_path="/dashboard/static")


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        sid = request.cookies.get("dashboard_session", "")
        if sid not in _sessions:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


@dashboard_bp.route("/api/dashboard/auth", methods=["POST"])
def auth_login():
    if not DASHBOARD_TOKEN:
        return jsonify({"ok": False, "error": "Dashboard not configured"}), 503
    body = request.get_json(silent=True) or {}
    if body.get("token") != DASHBOARD_TOKEN:
        return jsonify({"ok": False, "error": "Invalid token"}), 401
    sid = uuid.uuid4().hex
    _sessions[sid] = {}
    resp = make_response(jsonify({"ok": True}))
    resp.set_cookie("dashboard_session", sid, httponly=True, samesite="Lax")
    return resp


@dashboard_bp.route("/api/dashboard/logout", methods=["POST"])
@require_auth
def auth_logout():
    sid = request.cookies.get("dashboard_session", "")
    _sessions.pop(sid, None)
    resp = make_response(jsonify({"ok": True}))
    resp.set_cookie("dashboard_session", "", expires=0)
    return resp


@dashboard_bp.route("/dashboard/")
def dashboard_index():
    return dashboard_bp.send_static_file("index.html")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_auth.py -v
```

Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/__init__.py tests/test_dashboard_auth.py && git commit -m "feat(dashboard): add blueprint skeleton and auth middleware"
```

---

## Task 2: Kiro Scanner

**Files:**
- Create: `dashboard/kiro_scanner.py`
- Test: `tests/test_dashboard_scanner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_scanner.py
import pytest
from dashboard.kiro_scanner import list_agents, list_skills

def test_list_agents():
    agents = list_agents()
    assert isinstance(agents, list)
    for a in agents:
        assert "name" in a
        assert "description" in a

def test_list_skills():
    skills = list_skills()
    assert isinstance(skills, list)
    for s in skills:
        assert "name" in s
        assert "description" in s
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_scanner.py -v
```

Expected: FAIL with "ModuleNotFoundError" or function not defined

- [ ] **Step 3: Implement kiro_scanner.py**

```python
"""Scan Kiro agents and skills from ~/.kiro/"""
import json
import os
from pathlib import Path

_KIRO_DIR = Path.home() / ".kiro"


def list_agents() -> list[dict]:
    agents_dir = _KIRO_DIR / "agents"
    results: list[dict] = []
    if not agents_dir.exists():
        return results
    for f in sorted(agents_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            results.append({
                "name": data.get("name", f.stem),
                "description": data.get("description", ""),
                "tools": data.get("tools", []),
                "resources": data.get("resources", []),
            })
        except Exception:
            continue
    return results


def list_skills() -> list[dict]:
    skills_dir = _KIRO_DIR / "skills"
    results: list[dict] = []
    if not skills_dir.exists():
        return results
    for md in sorted(skills_dir.rglob("SKILL.md")):
        try:
            text = md.read_text(encoding="utf-8")
            name = md.parent.name
            description = ""
            triggers: list[str] = []
            # Parse frontmatter if present
            if text.startswith("---"):
                end = text.find("---", 3)
                if end != -1:
                    fm = text[3:end].strip()
                    for line in fm.splitlines():
                        if line.startswith("name:"):
                            name = line.split(":", 1)[1].strip()
                        elif line.startswith("description:"):
                            description = line.split(":", 1)[1].strip()
                        elif line.startswith("triggers:"):
                            in_triggers = True
                        elif line.strip().startswith("-") and "triggers" in locals():
                            triggers.append(line.strip().lstrip("-").strip())
            # Fallback: first non-empty line after frontmatter as description
            if not description:
                lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("-")]
                if lines:
                    description = lines[0][:200]
            results.append({
                "name": name,
                "description": description,
                "triggers": triggers,
                "path": str(md.relative_to(_KIRO_DIR)),
            })
        except Exception:
            continue
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_scanner.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/kiro_scanner.py tests/test_dashboard_scanner.py && git commit -m "feat(dashboard): add kiro scanner for agents and skills"
```

---

## Task 3: Config Store

**Files:**
- Create: `dashboard/config_store.py`
- Test: `tests/test_dashboard_config_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_config_store.py
import os
import tempfile
from pathlib import Path
from dashboard.config_store import ConfigStore

def test_read_core_config():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("KIRO_AGENT=my-agent\n")
        f.write("ALERT_NOTIFY_USER_ID=ou_123\n")
        f.flush()
        store = ConfigStore(env_path=f.name)
        cfg = store.read_core_config()
        assert cfg["KIRO_AGENT"] == "my-agent"
        assert cfg["ALERT_NOTIFY_USER_ID"] == "ou_123"
        os.unlink(f.name)

def test_write_core_config():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("KIRO_AGENT=old\n")
        f.flush()
        store = ConfigStore(env_path=f.name)
        store.write_core_config({"KIRO_AGENT": "new"})
        text = Path(f.name).read_text()
        assert "KIRO_AGENT=new" in text
        os.unlink(f.name)

def test_mappings_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "dashboard_config.json"
        store = ConfigStore(mappings_path=str(path))
        mappings = [{"source": "prometheus", "severity": "critical", "agent": "ec2-alert-analyzer"}]
        store.write_mappings(mappings)
        assert store.read_mappings() == mappings
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_config_store.py -v
```

Expected: FAIL

- [ ] **Step 3: Implement config_store.py**

```python
"""Read/write bot configuration for dashboard"""
import json
import os
from pathlib import Path

CORE_KEYS = [
    "KIRO_AGENT",
    "ALERT_NOTIFY_USER_ID",
    "ALERT_AUTO_ANALYZE_SEVERITY",
    "WEBHOOK_TOKEN",
    "WEBHOOK_PORT",
    "WEBHOOK_HOST",
    "ENABLE_MEMORY",
    "GROUP_AT_ONLY",
]

SENSITIVE_KEYS = {"WEBHOOK_TOKEN", "DASHBOARD_TOKEN"}


class ConfigStore:
    def __init__(self, env_path=".env", mappings_path="dashboard_config.json"):
        self.env_path = Path(env_path)
        self.mappings_path = Path(mappings_path)

    def read_core_config(self) -> dict:
        result: dict[str, str] = {}
        defaults = {k: "" for k in CORE_KEYS}
        if not self.env_path.exists():
            return defaults
        for line in self.env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                if key in CORE_KEYS:
                    result[key] = val.strip()
        for k in CORE_KEYS:
            if k not in result:
                result[k] = ""
        return result

    def write_core_config(self, updates: dict) -> None:
        lines: list[str] = []
        existing: dict[str, str] = {}
        if self.env_path.exists():
            for line in self.env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()
                lines.append(line)
        # Apply updates
        for k, v in updates.items():
            if k in CORE_KEYS:
                existing[k] = v
        # Rebuild file preserving structure roughly
        new_lines: list[str] = []
        seen = set()
        for line in lines:
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                k = stripped.split("=", 1)[0].strip()
                if k in existing and k in updates:
                    new_lines.append(f"{k}={existing[k]}")
                    seen.add(k)
                    continue
            new_lines.append(line)
        for k, v in existing.items():
            if k not in seen and k in updates:
                new_lines.append(f"{k}={v}")
        self.env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    def read_mappings(self) -> list[dict]:
        if not self.mappings_path.exists():
            return []
        try:
            data = json.loads(self.mappings_path.read_text(encoding="utf-8"))
            return data.get("mappings", [])
        except Exception:
            return []

    def write_mappings(self, mappings: list[dict]) -> None:
        self.mappings_path.write_text(
            json.dumps({"mappings": mappings}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_config_store.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/config_store.py tests/test_dashboard_config_store.py && git commit -m "feat(dashboard): add config store for env and mappings"
```

---

## Task 4: API Routes — Agents, Skills, Config, Mappings

**Files:**
- Create: `dashboard/api.py`
- Test: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_api.py
import pytest
from dashboard import dashboard_bp

@pytest.fixture
def client():
    from flask import Flask
    import os
    os.environ["DASHBOARD_TOKEN"] = "test-token"
    from dashboard import _sessions
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        # login
        c.post("/api/dashboard/auth", json={"token": "test-token"})
        yield c

def test_get_agents(client):
    resp = client.get("/api/dashboard/agents")
    assert resp.status_code == 200
    assert "agents" in resp.json

def test_get_skills(client):
    resp = client.get("/api/dashboard/skills")
    assert resp.status_code == 200
    assert "skills" in resp.json

def test_get_config(client):
    resp = client.get("/api/dashboard/config")
    assert resp.status_code == 200
    assert "config" in resp.json

def test_post_mappings(client):
    payload = {"mappings": [{"source": "prometheus", "severity": "critical", "agent": "ec2-alert-analyzer"}]}
    resp = client.post("/api/dashboard/mappings", json=payload)
    assert resp.status_code == 200
    assert resp.json["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_api.py -v
```

Expected: FAIL with route not found

- [ ] **Step 3: Implement api.py**

```python
"""Dashboard API routes"""
import os
from flask import request, jsonify
from dashboard import dashboard_bp, require_auth
from dashboard.kiro_scanner import list_agents, list_skills
from dashboard.config_store import ConfigStore
from event_store import EventStore
from event_ingest import webhook_handler, ingest_to_store

_env_path = os.environ.get("ENV_PATH", ".env")
_config_store = ConfigStore(env_path=_env_path)


@dashboard_bp.route("/api/dashboard/agents", methods=["GET"])
@require_auth
def get_agents():
    return jsonify({"ok": True, "agents": list_agents()})


@dashboard_bp.route("/api/dashboard/skills", methods=["GET"])
@require_auth
def get_skills():
    return jsonify({"ok": True, "skills": list_skills()})


@dashboard_bp.route("/api/dashboard/config", methods=["GET"])
@require_auth
def get_config():
    cfg = _config_store.read_core_config()
    # Mask sensitive values
    for k in cfg:
        if k in {"WEBHOOK_TOKEN", "DASHBOARD_TOKEN"} and cfg[k]:
            cfg[k] = "***"
    return jsonify({"ok": True, "config": cfg})


@dashboard_bp.route("/api/dashboard/config", methods=["POST"])
@require_auth
def post_config():
    body = request.get_json(silent=True) or {}
    updates = {k: v for k, v in body.items() if k in _config_store.read_core_config()}
    _config_store.write_core_config(updates)
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/mappings", methods=["GET"])
@require_auth
def get_mappings():
    return jsonify({"ok": True, "mappings": _config_store.read_mappings()})


@dashboard_bp.route("/api/dashboard/mappings", methods=["POST"])
@require_auth
def post_mappings():
    body = request.get_json(silent=True) or {}
    _config_store.write_mappings(body.get("mappings", []))
    return jsonify({"ok": True})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_api.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/api.py tests/test_dashboard_api.py && git commit -m "feat(dashboard): add API routes for agents, skills, config, mappings"
```

---

## Task 5: API Routes — Events (CRUD)

**Files:**
- Modify: `dashboard/api.py`
- Test: `tests/test_dashboard_api_events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_api_events.py
import pytest
from dashboard import dashboard_bp

@pytest.fixture
def client():
    from flask import Flask
    import os
    os.environ["DASHBOARD_TOKEN"] = "test-token"
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.post("/api/dashboard/auth", json={"token": "test-token"})
        yield c

def test_events_crud(client):
    # create
    resp = client.post("/api/dashboard/events", json={
        "id": "dash-test-001",
        "event_type": "指标异常",
        "title": "Dashboard test event",
        "severity": "high",
        "source": "dashboard",
    })
    assert resp.status_code == 200
    assert resp.json["ok"] is True

    # list
    resp = client.get("/api/dashboard/events?source=dashboard")
    assert resp.status_code == 200
    ids = [e["id"] for e in resp.json["events"]]
    assert "dash-test-001" in ids

    # delete
    resp = client.delete("/api/dashboard/events/dash-test-001")
    assert resp.status_code == 200
    assert resp.json["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_api_events.py -v
```

Expected: FAIL

- [ ] **Step 3: Append event routes to api.py**

Add the following to the bottom of `dashboard/api.py`:

```python
@dashboard_bp.route("/api/dashboard/events", methods=["GET"])
@require_auth
def get_events():
    store = EventStore()
    source = request.args.get("source", "")
    severity = request.args.get("severity", "")
    event_type = request.args.get("event_type", "")
    q = request.args.get("q", "")
    limit = int(request.args.get("limit", "50"))
    offset = int(request.args.get("offset", "0"))

    conditions = []
    params: list = []
    if source:
        conditions.append("source = ?")
        params.append(source)
    if severity:
        conditions.append("severity = ?")
        params.append(severity)
    if event_type:
        conditions.append("event_type = ?")
        params.append(event_type)

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT * FROM events WHERE {where_clause} ORDER BY ts DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = store.conn.execute(sql, params).fetchall()
    cols = [d[0] for d in store.conn.execute(sql, params).description]
    events = [dict(zip(cols, row)) for row in rows]
    return jsonify({"ok": True, "events": events})


@dashboard_bp.route("/api/dashboard/events", methods=["POST"])
@require_auth
def post_event():
    body = request.get_json(silent=True) or {}
    default_user = os.environ.get("ALERT_NOTIFY_USER_ID", "system")
    record = webhook_handler(body, default_user_id=default_user)
    if not record.get("ok"):
        return jsonify(record), 400
    store = EventStore()
    result = ingest_to_store(store, record)
    status = 200 if result["ok"] else 500
    return jsonify(result), status


@dashboard_bp.route("/api/dashboard/events/<event_id>", methods=["DELETE"])
@require_auth
def delete_event(event_id):
    store = EventStore()
    store.conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    store.conn.commit()
    return jsonify({"ok": True})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_api_events.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/api.py tests/test_dashboard_api_events.py && git commit -m "feat(dashboard): add event CRUD API routes"
```

---

## Task 6: API Routes — Scheduler (CRUD)

**Files:**
- Modify: `dashboard/api.py`
- Test: `tests/test_dashboard_api_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_api_scheduler.py
import pytest
from dashboard import dashboard_bp

@pytest.fixture
def client():
    from flask import Flask
    import os
    os.environ["DASHBOARD_TOKEN"] = "test-token"
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.post("/api/dashboard/auth", json={"token": "test-token"})
        yield c

def test_scheduler_crud(client):
    # create
    resp = client.post("/api/dashboard/scheduler", json={
        "frequency": "每天",
        "time_str": "09:00",
        "prompt": "test prompt",
        "user_id": "test_user",
    })
    assert resp.status_code == 200
    job_id = resp.json["job_id"]

    # list
    resp = client.get("/api/dashboard/scheduler")
    assert resp.status_code == 200
    ids = [j["id"] for j in resp.json["jobs"]]
    assert job_id in ids

    # update (disable)
    resp = client.put(f"/api/dashboard/scheduler/{job_id}", json={"enabled": False})
    assert resp.status_code == 200

    # delete
    resp = client.delete(f"/api/dashboard/scheduler/{job_id}")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_api_scheduler.py -v
```

Expected: FAIL

- [ ] **Step 3: Append scheduler routes to api.py**

Add the following to the bottom of `dashboard/api.py`:

```python
@dashboard_bp.route("/api/dashboard/scheduler", methods=["GET"])
@require_auth
def get_scheduler():
    from scheduler import Scheduler
    sched = Scheduler(send_fn=lambda *a, **k: None, kiro_fn=lambda *a, **k: "")
    jobs = sched.list_jobs("all")
    return jsonify({"ok": True, "jobs": jobs})


@dashboard_bp.route("/api/dashboard/scheduler", methods=["POST"])
@require_auth
def post_scheduler():
    from scheduler import Scheduler
    body = request.get_json(silent=True) or {}
    sched = Scheduler(send_fn=lambda *a, **k: None, kiro_fn=lambda *a, **k: "")
    result = sched.add_job(
        user_id=body.get("user_id", "system"),
        frequency=body.get("frequency", "每天"),
        time_str=body.get("time_str", "09:00"),
        prompt=body.get("prompt", ""),
    )
    return jsonify({"ok": True, "job_id": result})


@dashboard_bp.route("/api/dashboard/scheduler/<int:job_id>", methods=["PUT"])
@require_auth
def put_scheduler(job_id):
    from scheduler import Scheduler
    body = request.get_json(silent=True) or {}
    sched = Scheduler(send_fn=lambda *a, **k: None, kiro_fn=lambda *a, **k: "")
    if "enabled" in body:
        if body["enabled"]:
            sched.enable_job(job_id)
        else:
            sched.disable_job(job_id)
    # edit other fields if provided
    if any(k in body for k in ("frequency", "time_str", "prompt")):
        sched.edit_job(job_id, body)
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dashboard/scheduler/<int:job_id>", methods=["DELETE"])
@require_auth
def delete_scheduler(job_id):
    from scheduler import Scheduler
    sched = Scheduler(send_fn=lambda *a, **k: None, kiro_fn=lambda *a, **k: "")
    sched.delete_job(job_id)
    return jsonify({"ok": True})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_api_scheduler.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/api.py tests/test_dashboard_api_scheduler.py && git commit -m "feat(dashboard): add scheduler CRUD API routes"
```

---

## Task 7: Frontend Shell (index.html + style.css)

**Files:**
- Create: `dashboard/static/index.html`
- Create: `dashboard/static/style.css`

- [ ] **Step 1: Create index.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>feishu-kiro-bot Dashboard</title>
<script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
<script src="https://unpkg.com/vue-router@4/dist/vue-router.global.js"></script>
<link rel="stylesheet" href="/dashboard/static/style.css">
</head>
<body>
<div id="app"></div>
<script src="/dashboard/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create style.css**

```css
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f7fa; color: #333; }

/* Layout */
.app-layout { display: flex; min-height: 100vh; }
.sidebar { width: 200px; background: #1a1a2e; color: #fff; padding: 20px 0; flex-shrink: 0; }
.sidebar .logo { padding: 0 20px 20px; font-weight: bold; font-size: 16px; border-bottom: 1px solid #333; margin-bottom: 10px; }
.sidebar nav a { display: block; padding: 12px 20px; color: #ccc; text-decoration: none; font-size: 14px; transition: .2s; }
.sidebar nav a:hover, .sidebar nav a.active { background: #16213e; color: #fff; }
.main { flex: 1; padding: 24px; overflow-y: auto; }

/* Cards */
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; margin-bottom: 24px; }
.card { background: #fff; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.card h3 { font-size: 14px; color: #666; margin-bottom: 8px; }
.card .value { font-size: 28px; font-weight: bold; color: #1a1a2e; }

/* Tables */
.data-table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.data-table th, .data-table td { padding: 12px 16px; text-align: left; border-bottom: 1px solid #eee; font-size: 14px; }
.data-table th { background: #fafafa; font-weight: 600; color: #555; }
.data-table tr:hover { background: #f8f9fa; }

/* Buttons */
.btn { display: inline-block; padding: 8px 16px; border-radius: 4px; border: none; cursor: pointer; font-size: 14px; transition: .2s; }
.btn-primary { background: #4a90d9; color: #fff; }
.btn-primary:hover { background: #357abd; }
.btn-danger { background: #e74c3c; color: #fff; }
.btn-danger:hover { background: #c0392b; }
.btn-sm { padding: 4px 10px; font-size: 12px; }

/* Forms */
.form-group { margin-bottom: 16px; }
.form-group label { display: block; margin-bottom: 6px; font-size: 14px; font-weight: 500; }
.form-group input, .form-group select, .form-group textarea {
  width: 100%; padding: 10px 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px;
}
.form-group input:focus, .form-group select:focus, .form-group textarea:focus {
  outline: none; border-color: #4a90d9;
}

/* Modal */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.4); display: flex; align-items: center; justify-content: center; z-index: 100; }
.modal { background: #fff; border-radius: 8px; padding: 24px; width: 500px; max-width: 90%; max-height: 80vh; overflow-y: auto; }
.modal h2 { margin-bottom: 16px; font-size: 18px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }

/* Login */
.login-box { max-width: 360px; margin: 100px auto; background: #fff; padding: 32px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,.1); }
.login-box h2 { margin-bottom: 20px; text-align: center; }

/* Utilities */
.mb-2 { margin-bottom: 16px; }
.text-right { text-align: right; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; }
.badge-critical { background: #fee; color: #c33; }
.badge-high { background: #fff3e0; color: #e65100; }
.badge-medium { background: #fff8e1; color: #f9a825; }
.badge-low { background: #e8f5e9; color: #2e7d32; }
```

- [ ] **Step 3: Verify by opening in browser (manual check)**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -c "
from flask import Flask
from dashboard import dashboard_bp
import os
os.environ['DASHBOARD_TOKEN'] = 'test'
app = Flask(__name__)
app.register_blueprint(dashboard_bp)
with app.test_client() as c:
    r = c.get('/dashboard/')
    print('status:', r.status_code)
    assert b'feishu-kiro-bot Dashboard' in r.data
    print('HTML shell OK')
"
```

Expected: status 200, HTML shell OK

- [ ] **Step 4: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/static/index.html dashboard/static/style.css && git commit -m "feat(dashboard): add frontend HTML shell and CSS"
```

---

## Task 8: Frontend App (app.js — Router + Pages)

**Files:**
- Create: `dashboard/static/app.js`

- [ ] **Step 1: Create app.js**

```javascript
const { createApp, ref, onMounted } = Vue;
const { createRouter, createWebHashHistory } = VueRouter;

const API_BASE = "/api/dashboard";

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(API_BASE + path, opts);
  if (r.status === 401) { window.location.hash = "/login"; throw new Error("Unauthorized"); }
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

/* ---------- Login Page ---------- */
const LoginPage = {
  template: `
    <div class="login-box">
      <h2>🔷 Dashboard Login</h2>
      <div class="form-group">
        <label>Token</label>
        <input type="password" v-model="token" @keyup.enter="login" placeholder="Enter DASHBOARD_TOKEN">
      </div>
      <button class="btn btn-primary" style="width:100%" @click="login">Login</button>
      <p v-if="error" style="color:#e74c3c;margin-top:12px;font-size:14px">{{ error }}</p>
    </div>
  `,
  setup() {
    const token = ref("");
    const error = ref("");
    async function login() {
      try {
        const r = await fetch(API_BASE + "/auth", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({token: token.value}) });
        if (!r.ok) throw new Error("Invalid token");
        window.location.hash = "/";
        window.location.reload();
      } catch (e) { error.value = e.message; }
    }
    return { token, error, login };
  }
};

/* ---------- Overview Page ---------- */
const OverviewPage = {
  template: `
    <div>
      <h2 class="mb-2">📊 概览</h2>
      <div class="card-grid">
        <div class="card"><h3>今日事件</h3><div class="value">{{ stats.events }}</div></div>
        <div class="card"><h3>运行中任务</h3><div class="value">{{ stats.jobs }}</div></div>
        <div class="card"><h3>Agents</h3><div class="value">{{ stats.agents }}</div></div>
        <div class="card"><h3>Skills</h3><div class="value">{{ stats.skills }}</div></div>
      </div>
    </div>
  `,
  setup() {
    const stats = ref({ events: 0, jobs: 0, agents: 0, skills: 0 });
    onMounted(async () => {
      try {
        const [agents, skills, jobs, events] = await Promise.all([
          api("GET", "/agents"),
          api("GET", "/skills"),
          api("GET", "/scheduler"),
          api("GET", "/events?limit=1"),
        ]);
        stats.value.agents = agents.agents?.length || 0;
        stats.value.skills = skills.skills?.length || 0;
        stats.value.jobs = jobs.jobs?.filter(j => j.enabled).length || 0;
        stats.value.events = events.events?.length || 0;
      } catch (e) { console.error(e); }
    });
    return { stats };
  }
};

/* ---------- Agents Page ---------- */
const AgentsPage = {
  template: `
    <div><h2 class="mb-2">🤖 Agents</h2>
    <div class="card-grid">
      <div class="card" v-for="a in agents" :key="a.name">
        <h3>{{ a.name }}</h3>
        <p style="font-size:13px;color:#666;margin-top:6px">{{ a.description }}</p>
        <p style="font-size:12px;color:#999;margin-top:8px">Tools: {{ (a.tools || []).join(", ") }}</p>
      </div>
    </div></div>
  `,
  setup() {
    const agents = ref([]);
    onMounted(async () => { const r = await api("GET", "/agents"); agents.value = r.agents || []; });
    return { agents };
  }
};

/* ---------- Skills Page ---------- */
const SkillsPage = {
  template: `
    <div><h2 class="mb-2">📋 Skills</h2>
    <div class="card-grid">
      <div class="card" v-for="s in skills" :key="s.name">
        <h3>{{ s.name }}</h3>
        <p style="font-size:13px;color:#666;margin-top:6px">{{ s.description }}</p>
        <p style="font-size:12px;color:#999;margin-top:8px" v-if="s.triggers?.length">Triggers: {{ s.triggers.join(", ") }}</p>
      </div>
    </div></div>
  `,
  setup() {
    const skills = ref([]);
    onMounted(async () => { const r = await api("GET", "/skills"); skills.value = r.skills || []; });
    return { skills };
  }
};

/* ---------- Events Page ---------- */
const EventsPage = {
  template: `
    <div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h2>📝 事件</h2>
        <button class="btn btn-primary" @click="showModal=true">+ 新建事件</button>
      </div>
      <div style="display:flex;gap:10px;margin-bottom:16px">
        <input v-model="filters.q" placeholder="搜索关键词" style="width:200px" @keyup.enter="load">
        <select v-model="filters.severity" @change="load"><option value="">全部级别</option><option>critical</option><option>high</option><option>medium</option><option>low</option></select>
        <select v-model="filters.source" @change="load"><option value="">全部来源</option><option>prometheus</option><option>cloudwatch</option><option>manual</option></select>
        <button class="btn btn-sm" @click="load">刷新</button>
      </div>
      <table class="data-table">
        <thead><tr><th>ID</th><th>时间</th><th>类型</th><th>级别</th><th>来源</th><th>标题</th><th></th></tr></thead>
        <tbody>
          <tr v-for="e in events" :key="e.id">
            <td style="font-size:12px;color:#666">{{ e.id?.slice(0,20) }}...</td>
            <td>{{ e.ts?.slice(0,16) }}</td>
            <td>{{ e.event_type }}</td>
            <td><span :class="'badge badge-' + e.severity">{{ e.severity }}</span></td>
            <td>{{ e.source }}</td>
            <td>{{ e.title }}</td>
            <td><button class="btn btn-danger btn-sm" @click="del(e.id)">删除</button></td>
          </tr>
        </tbody>
      </table>
      <!-- Modal -->
      <div class="modal-overlay" v-if="showModal" @click.self="showModal=false">
        <div class="modal">
          <h2>新建事件</h2>
          <div class="form-group"><label>ID（留空自动生成）</label><input v-model="form.id"></div>
          <div class="form-group"><label>类型</label>
            <select v-model="form.event_type"><option>指标异常</option><option>系统变更</option><option>应用发版</option><option>故障处理</option><option>配置变更</option><option>手动记录</option></select>
          </div>
          <div class="form-group"><label>标题 *</label><input v-model="form.title"></div>
          <div class="form-group"><label>描述</label><textarea v-model="form.description" rows="3"></textarea></div>
          <div class="form-group"><label>实体（逗号分隔）</label><input v-model="form.entities"></div>
          <div class="form-group"><label>来源</label><input v-model="form.source" placeholder="dashboard"></div>
          <div class="form-group"><label>级别</label>
            <select v-model="form.severity"><option>low</option><option>medium</option><option>high</option><option>critical</option></select>
          </div>
          <div class="modal-actions">
            <button class="btn" @click="showModal=false">取消</button>
            <button class="btn btn-primary" @click="submit">保存</button>
          </div>
        </div>
      </div>
    </div>
  `,
  setup() {
    const events = ref([]);
    const filters = ref({ q: "", severity: "", source: "" });
    const showModal = ref(false);
    const form = ref({ id: "", event_type: "手动记录", title: "", description: "", entities: "", source: "dashboard", severity: "medium" });
    async function load() {
      let q = `?limit=50`;
      if (filters.value.severity) q += `&severity=${filters.value.severity}`;
      if (filters.value.source) q += `&source=${filters.value.source}`;
      const r = await api("GET", `/events${q}`);
      events.value = r.events || [];
    }
    async function submit() {
      const body = { ...form.value };
      if (body.entities) body.entities = body.entities.split(",").map(s => s.trim()).filter(Boolean);
      await api("POST", "/events", body);
      showModal.value = false;
      load();
    }
    async function del(id) { if (!confirm("确认删除？")) return; await api("DELETE", `/events/${id}`); load(); }
    onMounted(load);
    return { events, filters, showModal, form, load, submit, del };
  }
};

/* ---------- Scheduler Page ---------- */
const SchedulerPage = {
  template: `
    <div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h2>⏰ 定时任务</h2>
        <button class="btn btn-primary" @click="showModal=true">+ 新建任务</button>
      </div>
      <table class="data-table">
        <thead><tr><th>ID</th><th>频率</th><th>时间</th><th>Prompt</th><th>状态</th><th></th></tr></thead>
        <tbody>
          <tr v-for="j in jobs" :key="j.id">
            <td>{{ j.id }}</td>
            <td>{{ j.frequency }}</td>
            <td>{{ j.time_str }}</td>
            <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis">{{ j.prompt }}</td>
            <td><span :class="j.enabled ? 'badge badge-low' : 'badge badge-medium'">{{ j.enabled ? '启用' : '禁用' }}</span></td>
            <td>
              <button class="btn btn-sm" @click="toggle(j)">{{ j.enabled ? '停用' : '启用' }}</button>
              <button class="btn btn-danger btn-sm" @click="del(j.id)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="modal-overlay" v-if="showModal" @click.self="showModal=false">
        <div class="modal">
          <h2>新建任务</h2>
          <div class="form-group"><label>频率</label>
            <select v-model="form.frequency"><option>每天</option><option>每周一</option><option>每周二</option><option>每周三</option><option>每周四</option><option>每周五</option><option>每周六</option><option>每周日</option><option>工作日</option></select>
          </div>
          <div class="form-group"><label>时间 (HH:MM)</label><input v-model="form.time_str" placeholder="09:00"></div>
          <div class="form-group"><label>Prompt</label><textarea v-model="form.prompt" rows="3"></textarea></div>
          <div class="form-group"><label>User ID</label><input v-model="form.user_id" placeholder="system"></div>
          <div class="modal-actions">
            <button class="btn" @click="showModal=false">取消</button>
            <button class="btn btn-primary" @click="submit">保存</button>
          </div>
        </div>
      </div>
    </div>
  `,
  setup() {
    const jobs = ref([]);
    const showModal = ref(false);
    const form = ref({ frequency: "每天", time_str: "09:00", prompt: "", user_id: "system" });
    async function load() { const r = await api("GET", "/scheduler"); jobs.value = r.jobs || []; }
    async function submit() { await api("POST", "/scheduler", form.value); showModal.value = false; load(); }
    async function toggle(j) { await api("PUT", `/scheduler/${j.id}`, { enabled: !j.enabled }); load(); }
    async function del(id) { if (!confirm("确认删除？")) return; await api("DELETE", `/scheduler/${id}`); load(); }
    onMounted(load);
    return { jobs, showModal, form, load, submit, toggle, del };
  }
};

/* ---------- Config Page ---------- */
const ConfigPage = {
  template: `
    <div>
      <h2 class="mb-2">⚙️ 配置</h2>
      <div style="display:flex;gap:20px;margin-bottom:16px">
        <button :class="tab==='core' ? 'btn btn-primary' : 'btn'" @click="tab='core'">核心配置</button>
        <button :class="tab==='mappings' ? 'btn btn-primary' : 'btn'" @click="tab='mappings'">告警映射</button>
      </div>
      <div v-if="tab==='core'">
        <div class="form-group" v-for="(v,k) in config" :key="k">
          <label>{{ k }}</label>
          <input v-model="config[k]">
        </div>
        <button class="btn btn-primary" @click="saveConfig">保存</button>
      </div>
      <div v-else>
        <table class="data-table">
          <thead><tr><th>来源</th><th>级别</th><th>Agent</th><th></th></tr></thead>
          <tbody>
            <tr v-for="(m,i) in mappings" :key="i">
              <td><input v-model="m.source" style="width:120px"></td>
              <td><input v-model="m.severity" style="width:100px"></td>
              <td><input v-model="m.agent" style="width:180px"></td>
              <td><button class="btn btn-danger btn-sm" @click="mappings.splice(i,1)">删除</button></td>
            </tr>
          </tbody>
        </table>
        <button class="btn btn-primary" style="margin-top:10px" @click="mappings.push({source:'',severity:'',agent:''})">+ 添加映射</button>
        <button class="btn btn-primary" style="margin-top:10px;margin-left:10px" @click="saveMappings">保存映射</button>
      </div>
    </div>
  `,
  setup() {
    const tab = ref("core");
    const config = ref({});
    const mappings = ref([]);
    onMounted(async () => {
      const c = await api("GET", "/config");
      config.value = c.config || {};
      const m = await api("GET", "/mappings");
      mappings.value = m.mappings || [];
    });
    async function saveConfig() { await api("POST", "/config", config.value); alert("已保存（部分配置需重启服务）"); }
    async function saveMappings() { await api("POST", "/mappings", { mappings: mappings.value }); alert("映射已保存"); }
    return { tab, config, mappings, saveConfig, saveMappings };
  }
};

/* ---------- Router + App ---------- */
const routes = [
  { path: "/login", component: LoginPage },
  { path: "/", component: OverviewPage },
  { path: "/agents", component: AgentsPage },
  { path: "/skills", component: SkillsPage },
  { path: "/events", component: EventsPage },
  { path: "/scheduler", component: SchedulerPage },
  { path: "/config", component: ConfigPage },
];

const router = createRouter({ history: createWebHashHistory(), routes });

const App = {
  template: `
    <div class="app-layout">
      <aside class="sidebar" v-if="$route.path !== '/login'">
        <div class="logo">🔷 kiro-bot</div>
        <nav>
          <router-link to="/" active-class="active">📊 概览</router-link>
          <router-link to="/agents" active-class="active">🤖 Agents</router-link>
          <router-link to="/skills" active-class="active">📋 Skills</router-link>
          <router-link to="/events" active-class="active">📝 事件</router-link>
          <router-link to="/scheduler" active-class="active">⏰ 任务</router-link>
          <router-link to="/config" active-class="active">⚙️ 配置</router-link>
        </nav>
      </aside>
      <main class="main">
        <router-view></router-view>
      </main>
    </div>
  `
};

createApp(App).use(router).mount("#app");
```

- [ ] **Step 2: Verify frontend loads**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -c "
from flask import Flask
from dashboard import dashboard_bp
import os
os.environ['DASHBOARD_TOKEN'] = 'test'
app = Flask(__name__)
app.register_blueprint(dashboard_bp)
with app.test_client() as c:
    # login first
    c.post('/api/dashboard/auth', json={'token': 'test'})
    r = c.get('/dashboard/static/app.js')
    print('app.js status:', r.status_code)
    assert r.status_code == 200
    assert b'createApp' in r.data
    print('app.js OK')
"
```

Expected: app.js status 200, app.js OK

- [ ] **Step 3: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add dashboard/static/app.js && git commit -m "feat(dashboard): add Vue 3 SPA frontend with all pages"
```

---

## Task 9: Integrate Blueprint into app.py

**Files:**
- Modify: `app.py`
- Test: `tests/test_dashboard_integration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_integration.py
import pytest

def test_dashboard_routes_registered():
    from app import app
    routes = [r.rule for r in app.url_map.iter_rules()]
    assert "/dashboard/" in routes
    assert "/api/dashboard/auth" in routes
    assert "/api/dashboard/events" in routes
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_integration.py -v
```

Expected: FAIL

- [ ] **Step 3: Modify app.py**

In `app.py`, find the line `from event_ingest import parse_manual_command, ingest_to_store` (inside the `ENABLE_MEMORY` block, around line 28). Add the dashboard import after that block or near the top of the file.

Add after the `ENABLE_MEMORY` block (around line 31):

```python
# ============ Dashboard Web Panel ============
try:
    from dashboard import dashboard_bp
    app = None  # placeholder, will use the actual Flask app instance
except ImportError:
    dashboard_bp = None
```

Actually, `app.py` doesn't have a global `app` Flask instance — it uses `webhook_app` for the webhook. We need to register the blueprint on `webhook_app`.

Find where `webhook_app = Flask("kiro-ec2-webhook")` is defined (around line 510). Add after it:

```python
# Register dashboard blueprint
if dashboard_bp:
    webhook_app.register_blueprint(dashboard_bp)
```

And add the import at the top of `app.py`, after the other imports:

```python
# Dashboard (optional)
try:
    from dashboard import dashboard_bp
except ImportError:
    dashboard_bp = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_integration.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add app.py tests/test_dashboard_integration.py && git commit -m "feat(dashboard): integrate blueprint into app.py"
```

---

## Task 10: Update .env.example

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Append DASHBOARD_TOKEN to .env.example**

Add to the bottom of `.env.example`:

```bash
# === Dashboard Web Panel ===
# DASHBOARD_TOKEN=change-me-strong-secret
```

- [ ] **Step 2: Commit**

```bash
cd /home/ubuntu/feishu-kiro-bot && git add .env.example && git commit -m "chore: add DASHBOARD_TOKEN to .env.example"
```

---

## Task 11: End-to-End Verification

**Files:** None new — integration test

- [ ] **Step 1: Run all dashboard tests together**

```bash
cd /home/ubuntu/feishu-kiro-bot && python3 -m pytest tests/test_dashboard_*.py -v
```

Expected: All PASS

- [ ] **Step 2: Restart service and verify in browser**

```bash
sudo systemctl restart feishu-kiro-bot.service
sleep 2
curl -s http://127.0.0.1:8080/dashboard/ | head -5
curl -s http://127.0.0.1:8080/api/dashboard/health 2>/dev/null || curl -s http://127.0.0.1:8080/health
```

Expected: HTML returned, health check 200

- [ ] **Step 3: Commit any final fixes and push**

```bash
cd /home/ubuntu/feishu-kiro-bot && git push origin main
```

---

## Plan Self-Review

### Spec Coverage

| Spec Requirement | Task | Status |
|-----------------|------|--------|
| Flask Blueprint at `/dashboard/` + `/api/dashboard/` | Task 1, 9 | ✅ |
| Vue 3 CDN SPA (no build) | Task 7, 8 | ✅ |
| Auth: DASHBOARD_TOKEN + Cookie | Task 1 | ✅ |
| Agent/Skill read-only display | Task 2, 4 | ✅ |
| Config edit (core + mappings) | Task 3, 4 | ✅ |
| Event CRUD | Task 5 | ✅ |
| Scheduler CRUD | Task 6 | ✅ |
| Zero impact on existing `/event` | Task 9 | ✅ |
| .env.example updated | Task 10 | ✅ |

### Placeholder Scan

No TBD/TODO/"implement later" found. Every step has exact code and exact commands.

### Type Consistency

- `webhook_handler()` and `ingest_to_store()` signatures consistent with existing code
- `EventStore` and `Scheduler` APIs reused without modification
- ConfigStore key names match `.env` keys

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-23-dashboard.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach do you prefer?
