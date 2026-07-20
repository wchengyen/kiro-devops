# 遷移、版本回滾與端到端驗收實作計畫

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推薦）或 superpowers:executing-plans 逐任務實現此計畫。步驟使用復選框（`- [ ]`）語法追蹤進度。

**目標：** 為多飛書 App／多 AWS Profile 功能建立發布快照（release manifest）、dark deployment、離線設定、legacy-default 切換、漸進擴展、同版本與應用版本回滾工具、規模測試與 12 項端到端驗收，並完成最終 go/no-go gate。

**架構：** 新增 `scripts/` 下的獨立運維工具（release manifest、版本回滾、legacy-default 設定產生器、legacy smoke test、祕密掃描）與 `tests/` 下的規模測試；不修改計畫 1–4 已完成的任何 runtime 程式碼。所有上線與回滾流程以本計畫內的可執行檢查清單（runbook）形式落地。

**技術棧：** Python 3.10+、標準庫 `argparse`／`hashlib`／`json`／`sqlite3`／`subprocess`、bash、pytest、計畫 1–4 的 `multi_profile` 公開介面。

**依賴：** 計畫 1–4 必須全部完成並通過各自驗證：

1. `docs/superpowers/plans/2026-07-14-multi-profile-routing-core.md`（ConfigRegistry、TenantRouter、feature flag）
2. `docs/superpowers/plans/2026-07-14-multi-profile-runtime-session-isolation.md`（ContextRuntime、SessionStore、SessionCaptureCoordinator、記憶隔離）
3. 計畫 3：多 App 與群告警整合（AppManager、多 App Dispatcher、原 App 回覆、告警 ExecutionContext）
4. 計畫 4：Profile 健康與 Dashboard（ProfileHealthMonitor、Draft 驗證／發布／revision／回滾、STS 驗證、pending-restart）

**參考規格：** `docs/superpowers/specs/2026-07-14-multi-profile-multi-feishu-group-design.md` 第 19–23 節（遷移與漸進上線、回滾策略、規模測試、發布驗收標準、已知限制）。

**關鍵約束：** `MULTI_PROFILE_ENABLED=true` 的正式切換只能出現在本計畫（規格第 24 節），且只在任務 8 的切換步驟與任務 11 的 go 決議之後生效。本計畫執行前的所有部署必須保持 `MULTI_PROFILE_ENABLED=false`。

---

## 檔案結構

### 建立

- `scripts/release_manifest.py`：產生與驗證 release manifest；備份 `.env`、`dashboard_config.json`、`user_sessions.json` 與既有 SQLite（online backup），記錄 git commit、依賴鎖定與 systemd unit checksum。
- `scripts/rollback_to_release.py`：依 release manifest 執行應用版本回滾；例行回滾永不覆寫任何目前 SQLite 檔。
- `scripts/build_legacy_default_config.py`：由現有 `.env` 產生等價的 `legacy-default` Draft YAML，不切換流量。
- `scripts/legacy_smoke_test.sh`：legacy 模式 smoke test（服務存活、`/health`、Webhook `/event`、日誌無啟動錯誤）。
- `scripts/secret_leak_scan.sh`：掃描日誌與 Dashboard response 是否含 Secret 或 AWS credential。
- `tests/test_release_manifest.py`：manifest 內容、checksum、權限與 verify 模式。
- `tests/test_rollback_to_release.py`：回滾計畫產生、dry-run、SQLite 保護與 flag 強制關閉。
- `tests/test_build_legacy_default_config.py`：`.env` → Draft YAML 對應與完整驗證。
- `tests/test_multi_profile_scale.py`：Fake Adapter／Fake Runtime 規模測試（10 App／20 profile／100 路由／50 並行）。

### 修改

- 無。本計畫不修改任何既有程式檔；runtime 設定檔（`.env`、`multi_profile_config.yaml`）只在任務 6–8 的部署執行時由操作步驟改動，不屬於程式提交。

### 明確不修改

- `gateway.py`
- `message_handler.py`
- `kiro_executor.py`
- `session_router.py`
- `alert_analysis.py`
- `platform_dispatcher.py`
- `multi_profile/`（計畫 1–4 已完成的全部模組）
- `adapters/`
- `dashboard/`
- `start.sh`、`setup.sh`、`kiro-devops.service`（systemd unit 只在部署時由安裝流程更新，本計畫不改其 repo 版本）

---

## 執行前基線

- [ ] **步驟 1：記錄計畫 5 起始 SHA**

```bash
git rev-parse HEAD > .git/plan5-base-sha
cat .git/plan5-base-sha
```

預期：輸出計畫 4 完成後的 HEAD SHA。後續所有範圍驗證都讀取此檔，不使用 `HEAD~N`。

- [ ] **步驟 2：確認計畫 1–4 介面可用**

```bash
python3 - <<'PY'
from multi_profile import (
    ConfigError,
    ConfigRegistry,
    ContextRuntime,
    ExecutionContext,
    RouteNotFound,
    SessionStore,
    TaskAlreadyRunning,
    TaskRegistry,
    TenantRouter,
    config_path,
    is_enabled,
    load_config,
)
print("plan 1-4 public API import OK")
PY
```

預期：輸出 `plan 1-4 public API import OK`。若計畫 3–4 的 AppManager／ProfileHealthMonitor／Dashboard API 尚未合併，停止並先完成前序計畫。

- [ ] **步驟 3：確認完整測試基線為綠**

```bash
pytest -q
```

預期：0 failed。若有既有失敗，停止並依 systematic-debugging 確認基線，不得帶著紅色基線開始發布工程。

- [ ] **步驟 4：確認生產 `.env` 尚未啟用多 profile**

```bash
grep -E "^MULTI_PROFILE_ENABLED" .env || echo "MULTI_PROFILE_ENABLED 未設定（預設 false）"
```

預期：不存在該行，或值為 `false`。

---

### 任務 1：Release manifest 工具

**文件：**
- 建立：`scripts/release_manifest.py`
- 建立：`tests/test_release_manifest.py`

對應規格第 19.1 節。部署前必須能用單一命令產生可驗證的發布快照，內容包含 git commit、依賴鎖定資訊、systemd unit checksum、`.env`／`dashboard_config.json`／`user_sessions.json`／既有 SQLite 的備份與備份 checksum。

- [ ] **步驟 1：編寫失敗的 manifest 測試**

建立 `tests/test_release_manifest.py`：

```python
import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_manifest.py"


def run_script(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("FEISHU_APP_SECRET=topsecret\n", encoding="utf-8")
    (project / "dashboard_config.json").write_text("{}", encoding="utf-8")
    (project / "user_sessions.json").write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(project / "events.db")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('x')")
    conn.commit()
    conn.close()
    return project


def test_manifest_records_git_dependencies_systemd_and_backups(tmp_path):
    project = make_project(tmp_path)
    out_dir = tmp_path / "backups"

    result = run_script(
        "--project-dir", str(project),
        "--output-dir", str(out_dir),
        "--systemd-unit", str(tmp_path / "missing.service"),  # 不存在時記錄為 null，不失敗
    )

    assert result.returncode == 0, result.stderr
    manifests = list(out_dir.glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 1
    assert "created_at" in manifest
    assert set(manifest["git"]) >= {"commit", "branch", "dirty"}
    assert "python" in manifest["dependencies"]["pip_freeze"]
    assert manifest["systemd"]["unit_sha256"] is None  # unit 不存在時為 null

    backed_up = {entry["source"] for entry in manifest["backups"]}
    assert backed_up >= {".env", "dashboard_config.json", "user_sessions.json", "events.db"}
    for entry in manifest["backups"]:
        backup_file = manifests[0].parent / entry["backup"]
        assert backup_file.is_file()
        assert len(entry["sha256"]) == 64
        assert entry["bytes"] == backup_file.stat().st_size


def test_env_backup_is_not_world_readable(tmp_path):
    project = make_project(tmp_path)
    out_dir = tmp_path / "backups"

    result = run_script("--project-dir", str(project), "--output-dir", str(out_dir))
    assert result.returncode == 0, result.stderr
    manifest_path = next(out_dir.glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    env_entry = next(e for e in manifest["backups"] if e["source"] == ".env")
    mode = stat.S_IMODE((manifest_path.parent / env_entry["backup"]).stat().st_mode)
    assert mode == 0o600
    dir_mode = stat.S_IMODE(manifest_path.parent.stat().st_mode)
    assert dir_mode == 0o700


def test_sqlite_backup_is_consistent_online_copy(tmp_path):
    project = make_project(tmp_path)
    out_dir = tmp_path / "backups"

    result = run_script("--project-dir", str(project), "--output-dir", str(out_dir))
    assert result.returncode == 0, result.stderr
    manifest_path = next(out_dir.glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    db_entry = next(e for e in manifest["backups"] if e["source"] == "events.db")

    conn = sqlite3.connect(manifest_path.parent / db_entry["backup"])
    rows = conn.execute("SELECT v FROM t").fetchall()
    conn.close()
    assert rows == [("x",)]


def test_verify_mode_detects_tampered_backup(tmp_path):
    project = make_project(tmp_path)
    out_dir = tmp_path / "backups"

    result = run_script("--project-dir", str(project), "--output-dir", str(out_dir))
    assert result.returncode == 0, result.stderr
    manifest_path = next(out_dir.glob("*/manifest.json"))

    ok = run_script("--verify", str(manifest_path))
    assert ok.returncode == 0, ok.stderr

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    env_entry = next(e for e in manifest["backups"] if e["source"] == ".env")
    (manifest_path.parent / env_entry["backup"]).write_text("tampered", encoding="utf-8")

    bad = run_script("--verify", str(manifest_path))
    assert bad.returncode != 0
    assert "checksum mismatch" in bad.stderr


def test_missing_optional_files_are_skipped_not_fatal(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("A=1\n", encoding="utf-8")

    result = run_script("--project-dir", str(project), "--output-dir", str(tmp_path / "out"))
    assert result.returncode == 0, result.stderr
    manifest_path = next((tmp_path / "out").glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [e["source"] for e in manifest["backups"]] == [".env"]
```

- [ ] **步驟 2：執行測試並確認腳本不存在**

```bash
pytest -q tests/test_release_manifest.py
```

預期：FAIL，包含 `No such file or directory` 指向 `scripts/release_manifest.py`。

- [ ] **步驟 3：實作 release manifest 工具**

建立 `scripts/release_manifest.py`。遵循 `scripts/sync_resource_metrics.py` 的慣例：`#!/usr/bin/env python3`、模組 docstring 附使用範例、`argparse`、`logging`。關鍵邏輯：

```python
#!/usr/bin/env python3
"""Create or verify a release manifest for kiro-devops deployments.

Usage:
    python3 scripts/release_manifest.py                      # 在專案根目錄建立快照
    python3 scripts/release_manifest.py --verify <manifest>  # 驗證既有快照

產出：runtime/release-backups/<UTC 時間戳>/ 下的 manifest.json、備份檔與 sha256。
manifest.json 只記錄 checksum 與檔名，不記錄任何檔案內容或 Secret 值。
"""
import argparse
import datetime
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("release_manifest")

PLAIN_FILES = ("dashboard_config.json", "user_sessions.json")
SECRET_FILES = (".env",)          # 備份後 chmod 600
SQLITE_GLOBS = ("*.db", "memory_db/*.db")  # 使用 sqlite3 backup API 做 online-safe 複製
DEFAULT_SYSTEMD_UNIT = "/etc/systemd/system/kiro-devops.service"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_info(project_dir: Path) -> dict:
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=project_dir, capture_output=True, text=True, check=True,
        ).stdout.strip()

    return {
        "commit": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
    }


def pip_freeze() -> str:
    return subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        capture_output=True, text=True, check=True,
    ).stdout


def backup_sqlite(source: Path, dest: Path) -> None:
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()


def collect_backup_sources(project_dir: Path) -> list[tuple[str, Path, str]]:
    """回傳 (source 相對路徑, 絕對路徑, 類型)；類型為 plain/secret/sqlite。"""
    sources = []
    for name in SECRET_FILES:
        path = project_dir / name
        if path.is_file():
            sources.append((name, path, "secret"))
    for name in PLAIN_FILES:
        path = project_dir / name
        if path.is_file():
            sources.append((name, path, "plain"))
    for pattern in SQLITE_GLOBS:
        for path in sorted(project_dir.glob(pattern)):
            if path.is_file():
                sources.append((str(path.relative_to(project_dir)), path, "sqlite"))
    return sources


def create_manifest(project_dir: Path, output_dir: Path, systemd_unit: Path | None) -> Path:
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_dir = output_dir / timestamp
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(snapshot_dir, 0o700)  # 內含 .env 備份，不得讓其他使用者讀取

    backups = []
    for rel, source, kind in collect_backup_sources(project_dir):
        backup_name = rel.replace("/", "__") + ".bak"
        dest = snapshot_dir / backup_name
        if kind == "sqlite":
            backup_sqlite(source, dest)
        else:
            shutil.copy2(source, dest)
        if kind == "secret":
            os.chmod(dest, 0o600)
        backups.append({
            "source": rel,
            "backup": backup_name,
            "kind": kind,
            "sha256": sha256_file(dest),
            "bytes": dest.stat().st_size,
        })
        logger.info("backed up %s (%s)", rel, kind)

    unit_sha256 = None
    if systemd_unit is not None and systemd_unit.is_file():
        unit_sha256 = sha256_file(systemd_unit)

    manifest = {
        "schema_version": 1,
        "created_at": timestamp,
        "project_dir": str(project_dir),
        "git": git_info(project_dir),
        "dependencies": {"pip_freeze": pip_freeze()},
        "systemd": {"unit_path": str(systemd_unit) if systemd_unit else None,
                     "unit_sha256": unit_sha256},
        "backups": backups,
    }
    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    os.chmod(manifest_path, 0o600)
    logger.info("manifest written: %s", manifest_path)
    return manifest_path


def verify_manifest(manifest_path: Path) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures = 0
    for entry in manifest["backups"]:
        backup = manifest_path.parent / entry["backup"]
        if not backup.is_file():
            logger.error("missing backup: %s", entry["backup"])
            failures += 1
            continue
        actual = sha256_file(backup)
        if actual != entry["sha256"]:
            logger.error("checksum mismatch: %s", entry["backup"])
            failures += 1
    if failures:
        return 1
    logger.info("manifest OK: %d backups verified", len(manifest["backups"]))
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Create or verify a kiro-devops release manifest")
    parser.add_argument("--project-dir", default=".", help="專案根目錄（預設目前目錄）")
    parser.add_argument("--output-dir", default=None,
                        help="快照輸出目錄（預設 <project>/runtime/release-backups）")
    parser.add_argument("--systemd-unit", default=DEFAULT_SYSTEMD_UNIT,
                        help="systemd unit 路徑；不存在時 checksum 記為 null")
    parser.add_argument("--verify", metavar="MANIFEST", help="驗證既有 manifest 的備份 checksum")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args(argv)
    if args.verify:
        return verify_manifest(Path(args.verify))
    project_dir = Path(args.project_dir).resolve()
    output_dir = Path(args.output_dir) if args.output_dir else project_dir / "runtime" / "release-backups"
    systemd_unit = Path(args.systemd_unit) if args.systemd_unit else None
    create_manifest(project_dir, output_dir, systemd_unit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

注意事項：

- `runtime/` 已在計畫 1 加入 `.gitignore`，快照不會進版本控制。
- manifest 的 `git.dirty` 為 `true` 時不阻擋建立，但必須在 stderr 警告；發布前檢查清單（任務 6）要求 dirty 為 false。
- SQLite 一律走 `sqlite3.Connection.backup()`，不得直接 `cp` 使用中的 `.db` 檔。
- `memory_db/chroma.sqlite3` 等向量庫檔案同樣由 `memory_db/*.db` glob 涵蓋。

- [ ] **步驟 4：執行 manifest 測試**

```bash
pytest -q tests/test_release_manifest.py
```

預期：5 passed。

- [ ] **步驟 5：手動驗證真實專案快照**

```bash
python3 scripts/release_manifest.py
ls runtime/release-backups/
python3 scripts/release_manifest.py --verify "$(ls -d runtime/release-backups/*/ | tail -1)manifest.json"
```

預期：建立一個時間戳目錄；verify 輸出 `manifest OK`。測試後可保留該快照作為本計畫基線快照。

- [ ] **步驟 6：提交任務 1**

```bash
git add scripts/release_manifest.py tests/test_release_manifest.py
git commit -m "feat(發布): 加入 release manifest 快照工具"
```

---

### 任務 2：應用版本回滾工具

**文件：**
- 建立：`scripts/rollback_to_release.py`
- 建立：`tests/test_rollback_to_release.py`

對應規格第 20.3 節。工具依 release manifest 執行版本回滾；例行回滾**不得覆寫任何目前 SQLite 資料檔**（包含 `runtime/tenant_sessions.db`），升級前 SQLite 備份僅供災難復原且需顯式 `--disaster-restore`。

- [ ] **步驟 1：編寫失敗的回滾工具測試**

建立 `tests/test_rollback_to_release.py`：

```python
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rollback_to_release.py"


def run_script(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def make_manifest(tmp_path: Path) -> Path:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / ".env.bak").write_text("FEISHU_APP_ID=cli_old\n", encoding="utf-8")
    (snapshot / "events.db.bak").write_text("old-db", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "created_at": "20260714T000000Z",
        "project_dir": str(tmp_path / "project"),
        "git": {"commit": "abc1234", "branch": "main", "dirty": False},
        "dependencies": {"pip_freeze": "flask==2.0.0\n"},
        "systemd": {"unit_path": "/etc/systemd/system/kiro-devops.service", "unit_sha256": "0" * 64},
        "backups": [
            {"source": ".env", "backup": ".env.bak", "kind": "secret",
             "sha256": "0" * 64, "bytes": 1},
            {"source": "events.db", "backup": "events.db.bak", "kind": "sqlite",
             "sha256": "0" * 64, "bytes": 1},
        ],
    }
    path = snapshot / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_dry_run_prints_plan_without_mutating(tmp_path):
    manifest = make_manifest(tmp_path)

    result = run_script("--manifest", str(manifest), "--dry-run", "--no-systemctl")

    assert result.returncode == 0, result.stderr
    assert "git checkout abc1234" in result.stdout
    assert "MULTI_PROFILE_ENABLED=false" in result.stdout
    assert not (tmp_path / "project").exists() or not (tmp_path / "project" / ".env").exists()


def test_routine_rollback_never_restores_sqlite(tmp_path):
    manifest = make_manifest(tmp_path)

    result = run_script("--manifest", str(manifest), "--dry-run", "--no-systemctl")

    assert result.returncode == 0, result.stderr
    plan = result.stdout
    assert "events.db" not in "\n".join(
        line for line in plan.splitlines() if line.startswith("RESTORE")
    )
    assert "skip sqlite" in plan.lower() or "略過 SQLite" in plan


def test_disaster_restore_requires_explicit_flag_and_precheck(tmp_path):
    manifest = make_manifest(tmp_path)

    without_flag = run_script(
        "--manifest", str(manifest), "--dry-run", "--no-systemctl", "--restore-sqlite",
    )
    assert without_flag.returncode != 0
    assert "--disaster-restore" in without_flag.stderr


def test_restored_env_forces_multi_profile_disabled(tmp_path):
    manifest = make_manifest(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("MULTI_PROFILE_ENABLED=true\nOTHER=1\n", encoding="utf-8")

    result = run_script(
        "--manifest", str(manifest), "--dry-run", "--no-systemctl",
        "--project-dir", str(project),
    )

    assert result.returncode == 0, result.stderr
    assert "MULTI_PROFILE_ENABLED=false" in result.stdout
```

- [ ] **步驟 2：執行測試並確認腳本不存在**

```bash
pytest -q tests/test_rollback_to_release.py
```

預期：FAIL，包含 `No such file or directory` 指向 `scripts/rollback_to_release.py`。

- [ ] **步驟 3：實作回滾工具**

建立 `scripts/rollback_to_release.py`。關鍵邏輯：

```python
#!/usr/bin/env python3
"""Roll back kiro-devops to the state recorded in a release manifest.

例行版本回滾（規格 20.3）：
    python3 scripts/rollback_to_release.py --manifest <snapshot>/manifest.json --yes

規則：
- 例行回滾只恢復程式（git checkout）、依賴、.env、dashboard_config.json 與 systemd unit。
- 永不覆寫任何目前 SQLite 檔（events.db、memory_db/*.db、runtime/tenant_sessions.db）。
- 升級前 SQLite 備份僅供災難復原，需同時給 --restore-sqlite --disaster-restore，
  且執行前先把目前 SQLite 另行複製到 <snapshot>/pre-disaster-restore/。
- 恢復的 .env 一律強制 MULTI_PROFILE_ENABLED=false。
"""
import argparse
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("rollback_to_release")

FORCED_FLAG = "MULTI_PROFILE_ENABLED=false"


def build_plan(manifest: dict, args) -> list[tuple[str, str]]:
    """回傳有序 (action, detail)；dry-run 只輸出，不執行。"""
    snapshot_dir = Path(args.manifest).parent
    project_dir = Path(args.project_dir)
    plan = [("STOP", "sudo systemctl stop kiro-devops")]
    plan.append(("GIT", f"git checkout {manifest['git']['commit']}"))
    freeze_file = snapshot_dir / "requirements-frozen.txt"
    plan.append(("WRITE", f"{freeze_file}（manifest pip_freeze）"))
    plan.append(("PIP", f"{sys.executable} -m pip install -r {freeze_file}"))
    for entry in manifest["backups"]:
        if entry["kind"] == "sqlite":
            plan.append(("SKIP", f"略過 SQLite 備份 {entry['source']}（例行回滾不覆寫資料檔）"))
        elif entry["source"] in (".env", "dashboard_config.json", "user_sessions.json"):
            plan.append(("RESTORE", f"{entry['backup']} -> {project_dir / entry['source']}"))
    plan.append(("ENV", f"強制 {FORCED_FLAG}"))
    if manifest["systemd"].get("unit_sha256"):
        plan.append(("RESTORE", "systemd unit + daemon-reload"))
    plan.append(("START", "sudo systemctl start kiro-devops"))
    plan.append(("SMOKE", "bash scripts/legacy_smoke_test.sh"))
    return plan


def force_flag_disabled(env_path: Path) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip("export ").strip()
        if stripped.startswith("MULTI_PROFILE_ENABLED="):
            prefix = "export " if line.strip().startswith("export") else ""
            lines[index] = f"{prefix}{FORCED_FLAG}"
            break
    else:
        lines.append(FORCED_FLAG)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(cmd: list[str], *, cwd=None, dry_run=False):
    logger.info("+ %s", " ".join(cmd))
    if not dry_run:
        subprocess.run(cmd, cwd=cwd, check=True)
```

實作要點：

- `--dry-run` 印出 `build_plan` 的全部動作後結束，不建立任何檔案。
- 非 dry-run 且未給 `--yes` 時互動確認；`git checkout` 前若工作區 dirty 則中止並提示先 `git stash` 或提交。
- 恢復 `.env` 後必須呼叫 `force_flag_disabled`；`user_sessions.json` 屬於 legacy 模式 Session 檔，例行回滾需要恢復（規格 20.2 第 3 點），SQLite 不恢復。
- `--restore-sqlite` 單獨給予時直接報錯退出，訊息必須含 `--disaster-restore`；災難復原前先把目前所有 SQLite 複製到 `runtime/disaster-restore-backup-<時間戳>/`。
- 完成後輸出後續手動步驟提示：執行 `scripts/legacy_smoke_test.sh` 與 `scripts/secret_leak_scan.sh`。

- [ ] **步驟 4：執行回滾工具測試**

```bash
pytest -q tests/test_rollback_to_release.py
```

預期：4 passed。

- [ ] **步驟 5：以任務 1 的真實快照做 dry-run 驗證**

```bash
python3 scripts/rollback_to_release.py \
  --manifest "$(ls -d runtime/release-backups/*/ | tail -1)manifest.json" \
  --dry-run --no-systemctl
```

預期：印出完整回滾計畫；`RESTORE` 行不含任何 `.db`；包含 `MULTI_PROFILE_ENABLED=false`。

- [ ] **步驟 6：提交任務 2**

```bash
git add scripts/rollback_to_release.py tests/test_rollback_to_release.py
git commit -m "feat(發布): 加入依 manifest 的應用版本回滾工具"
```

---

### 任務 3：legacy-default 離線設定產生器

**文件：**
- 建立：`scripts/build_legacy_default_config.py`
- 建立：`tests/test_build_legacy_default_config.py`

對應規格第 19.3 節。由現有 `.env` 產生等價 Draft：現有 App 使用原 env key（`FEISHU_APP_ID`／`FEISHU_APP_SECRET`），建立 `legacy-default` profile，並把 `FEISHU_POLL_CHAT_IDS` 的所有已知群映射到 `legacy-default`。只產生與驗證設定，不切換流量。

- [ ] **步驟 1：編寫失敗的產生器測試**

建立 `tests/test_build_legacy_default_config.py`：

```python
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_legacy_default_config.py"

ENV_TEXT = """
FEISHU_APP_ID=cli_current
FEISHU_APP_SECRET=current_secret
FEISHU_POLL_CHAT_IDS=oc_alpha, oc_beta ,oc_gamma
KIRO_AGENT=my-dev-bot
DEFAULT_MODEL=claude-sonnet
BACKGROUND_MODEL=claude-haiku
KIRO_SYNC_TIMEOUT=180
KIRO_ASYNC_TIMEOUT=2400
ALERT_ANALYZE_TIMEOUT=240
AWS_PROFILE=production
AWS_REGION=cn-northwest-1
"""


def run_script(*args, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_generates_legacy_default_draft(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_TEXT, encoding="utf-8")

    result = run_script(
        "--env-file", str(env_file),
        "--account-id", "123456789012",
        "--working-dir", str(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    draft = yaml.safe_load(result.stdout)
    assert draft["version"] == 1

    app = draft["apps"]["legacy-bot"]
    assert app["app_id_env"] == "FEISHU_APP_ID"
    assert app["app_secret_env"] == "FEISHU_APP_SECRET"
    assert app["default_profile"] == "legacy-default"
    assert "current_secret" not in result.stdout  # 只引用變數名，不輸出 Secret 值

    profile = draft["profiles"]["legacy-default"]
    assert profile["aws_profile"] == "production"
    assert profile["aws_region"] == "cn-northwest-1"
    assert profile["expected_account_id"] == "123456789012"
    assert profile["kiro_agent"] == "my-dev-bot"
    assert profile["model"] == "claude-sonnet"
    assert profile["alert_model"] == "claude-haiku"
    assert profile["sync_timeout"] == 180
    assert profile["async_timeout"] == 2400
    assert profile["alert_timeout"] == 240

    routes = {(r["app"], r["chat_id"]): r for r in draft["routes"]}
    assert set(routes) == {
        ("legacy-bot", "oc_alpha"),
        ("legacy-bot", "oc_beta"),
        ("legacy-bot", "oc_gamma"),
    }
    assert all(route["poll_alerts"] is True for route in routes.values())
    assert all(route["profile"] == "legacy-default" for route in routes.values())


def test_defaults_when_optional_env_missing(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FEISHU_APP_ID=cli_current\nFEISHU_APP_SECRET=s\n", encoding="utf-8",
    )

    result = run_script(
        "--env-file", str(env_file),
        "--account-id", "123456789012",
        "--working-dir", str(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    draft = yaml.safe_load(result.stdout)
    profile = draft["profiles"]["legacy-default"]
    assert profile["aws_profile"] == "default"
    assert profile.get("aws_region") is None
    assert profile.get("kiro_agent") is None
    assert profile.get("model") is None
    assert profile.get("alert_model") is None
    assert profile["sync_timeout"] == 120
    assert profile["async_timeout"] == 1800
    assert profile["alert_timeout"] == 300
    assert draft["routes"] == []


def test_output_is_loadable_by_plan1_loader(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_TEXT, encoding="utf-8")

    generated = run_script(
        "--env-file", str(env_file),
        "--account-id", "123456789012",
        "--working-dir", str(tmp_path),
    )
    assert generated.returncode == 0, generated.stderr
    config_path = tmp_path / "multi_profile_config.yaml"
    config_path.write_text(generated.stdout, encoding="utf-8")

    from multi_profile import load_config

    snapshot = load_config(
        config_path,
        environ={"FEISHU_APP_ID": "cli_current", "FEISHU_APP_SECRET": "s"},
    )
    assert snapshot.profiles["legacy-default"].aws_profile == "production"
    assert len(snapshot.routes) == 3


def test_refuses_to_overwrite_existing_config_without_force(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_TEXT, encoding="utf-8")
    existing = tmp_path / "multi_profile_config.yaml"
    existing.write_text("version: 1\n", encoding="utf-8")

    result = run_script(
        "--env-file", str(env_file),
        "--account-id", "123456789012",
        "--working-dir", str(tmp_path),
        "--output", str(existing),
    )

    assert result.returncode != 0
    assert "--force" in result.stderr
    assert existing.read_text(encoding="utf-8") == "version: 1\n"


def test_invalid_account_id_rejected(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(ENV_TEXT, encoding="utf-8")

    result = run_script(
        "--env-file", str(env_file),
        "--account-id", "123",
        "--working-dir", str(tmp_path),
    )

    assert result.returncode != 0
    assert "expected_account_id" in result.stderr or "12" in result.stderr
```

- [ ] **步驟 2：執行測試並確認腳本不存在**

```bash
pytest -q tests/test_build_legacy_default_config.py
```

預期：FAIL，包含 `No such file or directory` 指向 `scripts/build_legacy_default_config.py`。

- [ ] **步驟 3：實作產生器**

建立 `scripts/build_legacy_default_config.py`。關鍵邏輯：

```python
#!/usr/bin/env python3
"""Build a legacy-default multi-profile draft from the current .env.

Usage:
    python3 scripts/build_legacy_default_config.py \
        --account-id 123456789012 --working-dir /home/ubuntu/kiro-devops \
        > multi_profile_config.draft.yaml

對應規格 19.3：現有 App 沿用原 env key；建立等價 legacy-default profile；
FEISHU_POLL_CHAT_IDS 全部映射到 legacy-default。只產生 Draft，不切換流量。
輸出絕不包含 FEISHU_APP_SECRET 的值，只引用環境變數名稱。
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

APP_KEY = "legacy-bot"
PROFILE_ID = "legacy-default"


def parse_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip().removeprefix("export ").strip()] = value.strip().strip('"').strip("'")
    return values


def build_draft(env: dict, account_id: str, working_dir: str) -> dict:
    profile = {
        "enabled": True,
        "aws_profile": env.get("AWS_PROFILE") or "default",
        "expected_account_id": account_id,
        "working_dir": working_dir,
        "sync_timeout": int(env.get("KIRO_SYNC_TIMEOUT") or env.get("KIRO_TIMEOUT") or 120),
        "async_timeout": int(env.get("KIRO_ASYNC_TIMEOUT") or 1800),
        "alert_timeout": int(env.get("ALERT_ANALYZE_TIMEOUT") or 300),
    }
    optional = {
        "aws_region": env.get("AWS_REGION"),
        "kiro_agent": env.get("KIRO_AGENT"),
        "model": env.get("DEFAULT_MODEL"),
        "alert_model": env.get("BACKGROUND_MODEL"),
    }
    profile.update({key: value for key, value in optional.items() if value})

    poll_chats = [
        chat.strip()
        for chat in env.get("FEISHU_POLL_CHAT_IDS", "").split(",")
        if chat.strip()
    ]
    return {
        "version": 1,
        "apps": {
            APP_KEY: {
                "enabled": True,
                "app_id_env": "FEISHU_APP_ID",
                "app_secret_env": "FEISHU_APP_SECRET",
                "default_profile": PROFILE_ID,
            }
        },
        "profiles": {PROFILE_ID: profile},
        "routes": [
            {"app": APP_KEY, "chat_id": chat, "profile": PROFILE_ID, "poll_alerts": True}
            for chat in poll_chats
        ],
    }
```

實作要點：

- `--account-id` 必須符合 `^\d{12}$`；腳本不猜測 Account ID，操作者用 `aws sts get-caller-identity --profile <name> --query Account --output text` 先取得。
- 預設輸出到 stdout；`--output` 覆寫已存在檔案需 `--force`。
- 產生後立即以計畫 1 的 `load_config` 自我驗證（`--validate` 預設開啟），失敗則非零退出且不輸出 YAML。
- `KIRO_TIMEOUT` 作為 `sync_timeout` 的 fallback，與 `.env.example` 的現有鍵一致。

- [ ] **步驟 4：執行產生器測試**

```bash
pytest -q tests/test_build_legacy_default_config.py
```

預期：5 passed。

- [ ] **步驟 5：提交任務 3**

```bash
git add scripts/build_legacy_default_config.py tests/test_build_legacy_default_config.py
git commit -m "feat(發布): 加入 legacy-default 離線設定產生器"
```

---

### 任務 4：規模測試（Fake Adapter + Fake Runtime）

**文件：**
- 建立：`tests/test_multi_profile_scale.py`

對應規格第 21.4 節。使用 Fake Adapter 與 Fake Runtime 模擬 10 個 App、20 個 profile、100 條群映射、50 個並行訊息，確認路由正確、沒有共享狀態污染，且 Registry 熱載入不阻塞既有任務。此測試只依賴計畫 1–2 的公開介面（`TenantRouter`、`ConfigRegistry`、`TaskRegistry`、`SessionStore`）與本測試自建的 Fake，不啟動真實子程序、不依賴計畫 3–4 的 AppManager／Dashboard。

- [ ] **步驟 1：建立規模測試**

建立 `tests/test_multi_profile_scale.py`：

```python
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from multi_profile import (
    ConfigRegistry,
    SessionStore,
    TaskAlreadyRunning,
    TaskRegistry,
    TenantRouter,
)

APP_COUNT = 10
PROFILE_COUNT = 20
ROUTE_COUNT = 100
CONCURRENT_MESSAGES = 50


def build_yaml(tmp_path) -> str:
    apps = "\n".join(
        f"""  app-{i:02d}:
    app_id_env: FEISHU_APP_{i:02d}_ID
    app_secret_env: FEISHU_APP_{i:02d}_SECRET
    default_profile: profile-{i % PROFILE_COUNT:02d}"""
        for i in range(APP_COUNT)
    )
    profiles = "\n".join(
        f"""  profile-{i:02d}:
    aws_profile: aws-{i:02d}
    expected_account_id: "{100000000000 + i}"
    working_dir: {tmp_path}"""
        for i in range(PROFILE_COUNT)
    )
    routes = "\n".join(
        f"""  - app: app-{i % APP_COUNT:02d}
    chat_id: oc_chat_{i:03d}
    profile: profile-{i % PROFILE_COUNT:02d}"""
        for i in range(ROUTE_COUNT)
    )
    return f"version: 1\napps:\n{apps}\nprofiles:\n{profiles}\nroutes:\n{routes}\n"


def build_environ() -> dict:
    env = {}
    for i in range(APP_COUNT):
        env[f"FEISHU_APP_{i:02d}_ID"] = f"cli_{i:02d}"
        env[f"FEISHU_APP_{i:02d}_SECRET"] = f"secret_{i:02d}"
    return env


class FakeAdapter:
    """記錄每則訊息由哪個 App 收到與回覆，取代真實 FeishuAdapter。"""

    def __init__(self, app_key):
        self.app_key = app_key
        self.replies = []
        self._lock = threading.Lock()

    def reply(self, chat_id, user_id, text):
        with self._lock:
            self.replies.append((chat_id, user_id, text))


class FakeRuntime:
    """以 principal_key 向 TaskRegistry 保留任務並寫入 SessionStore，取代真實 Kiro 子程序。"""

    def __init__(self, tasks: TaskRegistry, sessions: SessionStore):
        self._tasks = tasks
        self._sessions = sessions

    def handle(self, context, delay=0.0):
        handle = self._tasks.reserve(context.principal_key)
        try:
            if delay:
                time.sleep(delay)
            self._sessions.register(
                principal_key=context.principal_key,
                kiro_session_id=f"uuid-{context.principal_key}",
                profile_id=context.profile_id,
                profile_fingerprint=context.profile_fingerprint,
            )
            return f"{context.app_key}|{context.profile_id}|{context.chat_id}"
        finally:
            self._tasks.release(context.principal_key)


@pytest.fixture
def scale_env(tmp_path):
    config_file = tmp_path / "multi_profile_config.yaml"
    config_file.write_text(build_yaml(tmp_path), encoding="utf-8")
    registry = ConfigRegistry(config_file, environ=build_environ())
    registry.load_initial()
    tasks = TaskRegistry()
    sessions = SessionStore(tmp_path / "tenant_sessions.db")
    adapters = {f"app-{i:02d}": FakeAdapter(f"app-{i:02d}") for i in range(APP_COUNT)}
    runtime = FakeRuntime(tasks, sessions)
    return registry, tasks, sessions, adapters, runtime


def test_scale_routing_is_correct_for_all_routes(scale_env):
    registry, *_ = scale_env
    router = TenantRouter(registry.snapshot())

    for i in range(ROUTE_COUNT):
        app_key = f"app-{i % APP_COUNT:02d}"
        context = router.resolve(
            platform="feishu",
            app_key=app_key,
            chat_type="group",
            chat_id=f"oc_chat_{i:03d}",
            user_id="ou_user",
        )
        assert context.profile_id == f"profile-{i % PROFILE_COUNT:02d}"
        assert context.principal_key == f"feishu/{app_key}/group/oc_chat_{i:03d}/user/ou_user"


def test_fifty_concurrent_messages_have_no_shared_state_pollution(scale_env):
    registry, tasks, sessions, adapters, runtime = scale_env
    router = TenantRouter(registry.snapshot())

    def handle_message(i):
        app_key = f"app-{i % APP_COUNT:02d}"
        chat_id = f"oc_chat_{i:03d}"
        context = router.resolve(
            platform="feishu",
            app_key=app_key,
            chat_type="group",
            chat_id=chat_id,
            user_id=f"ou_user_{i % 7}",
        )
        result = runtime.handle(context)
        adapters[app_key].reply(chat_id, context.user_id, result)
        return context, result

    with ThreadPoolExecutor(max_workers=CONCURRENT_MESSAGES) as pool:
        outcomes = list(pool.map(handle_message, range(CONCURRENT_MESSAGES)))

    for i, (context, result) in enumerate(outcomes):
        expected_app = f"app-{i % APP_COUNT:02d}"
        assert result == f"{expected_app}|profile-{i % PROFILE_COUNT:02d}|oc_chat_{i:03d}"

    # 回覆全部來自原 App、原群
    total_replies = sum(len(a.replies) for a in adapters.values())
    assert total_replies == CONCURRENT_MESSAGES
    for app_key, adapter in adapters.items():
        for chat_id, _user, text in adapter.replies:
            assert text.startswith(f"{app_key}|")

    # Session 全部以各自的 principal_key 落庫，沒有互相覆寫
    principals = {context.principal_key for context, _ in outcomes}
    for context, _ in outcomes:
        record = sessions.resolve(context.principal_key)
        assert record is not None
        assert record.profile_id == context.profile_id
        assert record.kiro_session_id == f"uuid-{context.principal_key}"
    assert len(principals) == CONCURRENT_MESSAGES  # (app, chat, user) 全不同


def test_same_principal_second_task_rejected_under_load(scale_env):
    registry, tasks, _, _, runtime = scale_env
    router = TenantRouter(registry.snapshot())
    context = router.resolve(
        platform="feishu", app_key="app-00", chat_type="group",
        chat_id="oc_chat_000", user_id="ou_user_0",
    )

    first = tasks.reserve(context.principal_key)
    try:
        with pytest.raises(TaskAlreadyRunning):
            runtime.handle(context)
    finally:
        tasks.release(context.principal_key)


def test_hot_reload_does_not_block_in_flight_tasks(scale_env, tmp_path):
    registry, tasks, sessions, adapters, runtime = scale_env
    in_flight = []
    started = threading.Event()

    def slow_message(i):
        router = TenantRouter(registry.snapshot())  # 訊息開始時取一次 snapshot
        context = router.resolve(
            platform="feishu",
            app_key=f"app-{i % APP_COUNT:02d}",
            chat_type="group",
            chat_id=f"oc_chat_{i:03d}",
            user_id=f"ou_user_{i % 7}",
        )
        in_flight.append(context)
        if i == 0:
            started.set()
        return runtime.handle(context, delay=0.3)

    with ThreadPoolExecutor(max_workers=CONCURRENT_MESSAGES) as pool:
        futures = [pool.submit(slow_message, i) for i in range(CONCURRENT_MESSAGES)]
        assert started.wait(timeout=5)

        reload_start = time.monotonic()
        new_snapshot = registry.reload()  # 設定未變更，generation 仍應遞增
        reload_elapsed = time.monotonic() - reload_start

        results = [f.result(timeout=10) for f in futures]

    assert new_snapshot.generation == 2
    assert reload_elapsed < 1.0  # 熱載入不得被 50 個進行中任務阻塞
    assert all(in_flight_ctx.config_generation == 1 for in_flight_ctx in in_flight)
    assert len(results) == CONCURRENT_MESSAGES

    # 熱載入後的新訊息使用新 generation
    context = TenantRouter(registry.snapshot()).resolve(
        platform="feishu", app_key="app-00", chat_type="group",
        chat_id="oc_chat_000", user_id="ou_new",
    )
    assert context.config_generation == 2
```

注意：上述 `SessionStore.register`／`resolve` 與 `TaskRegistry.reserve`／`release` 的確切簽名以計畫 2 完成後的公開介面為準；若簽名不同，調整 FakeRuntime 呼叫，不得修改計畫 2 的實作來遷就測試。

- [ ] **步驟 2：執行規模測試**

```bash
pytest -q tests/test_multi_profile_scale.py -v
```

預期：4 passed。

- [ ] **步驟 3：確認規模測試無真實外部依賴**

```bash
pytest -q tests/test_multi_profile_scale.py -v --deselect nothing 2>/dev/null; \
  grep -n "subprocess\|aws \|kiro-cli\|socket" tests/test_multi_profile_scale.py || echo "no external calls"
```

預期：輸出 `no external calls`；測試不觸及 AWS、Kiro CLI 或網路。

- [ ] **步驟 4：提交任務 4**

```bash
git add tests/test_multi_profile_scale.py
git commit -m "test(多租戶): 加入 10 App／20 profile／100 路由規模測試"
```

---

### 任務 5：Legacy smoke test 與祕密掃描腳本

**文件：**
- 建立：`scripts/legacy_smoke_test.sh`
- 建立：`scripts/secret_leak_scan.sh`

供 dark deployment（任務 6）、緊急回滾（任務 9）與版本回滾工具（任務 2）共用。遵循 `scripts/monitor_alert_chain.sh` 的 bash 風格與中文註解。

- [ ] **步驟 1：建立 legacy smoke test 腳本**

建立 `scripts/legacy_smoke_test.sh`：

```bash
#!/bin/bash
# Legacy 模式 smoke test：服務存活、/health、Webhook /event、啟動日誌無錯誤
# 用法：bash scripts/legacy_smoke_test.sh
# 依賴：.env 中的 WEBHOOK_PORT（預設 8080）與 WEBHOOK_TOKEN
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

FAILED=0
check() {  # check <名稱> <0=通過>
    if [ "$2" -eq 0 ]; then
        echo "PASS  $1"
    else
        echo "FAIL  $1"
        FAILED=1
    fi
}

if [ -f .env ]; then
    set -a; source .env; set +a
fi
PORT="${WEBHOOK_PORT:-8080}"
HOST="${WEBHOOK_HOST:-127.0.0.1}"
[ "$HOST" = "0.0.0.0" ] && HOST="127.0.0.1"

# 1. systemd 服務存活
systemctl is-active --quiet kiro-devops
check "systemd kiro-devops is-active" $?

# 2. /health 回 200
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://${HOST}:${PORT}/health")
[ "$CODE" = "200" ]
check "GET /health -> 200（實際: ${CODE}）" $?

# 3. Webhook /event 未授權回 401
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -X POST \
    "http://${HOST}:${PORT}/event" -H 'Content-Type: application/json' -d '{}')
[ "$CODE" = "401" ]
check "POST /event 無 token -> 401（實際: ${CODE}）" $?

# 4. Webhook /event 帶 token、低嚴重度（不觸發分析）可入庫
CODE=$(curl -s -o /tmp/legacy_smoke_event.json -w '%{http_code}' --max-time 10 -X POST \
    "http://${HOST}:${PORT}/event" \
    -H "Authorization: Bearer ${WEBHOOK_TOKEN}" \
    -H 'Content-Type: application/json' \
    -d '{"source":"legacy-smoke","title":"smoke test event","severity":"info","message":"smoke"}')
[ "$CODE" = "200" ] && grep -q '"ok": *true' /tmp/legacy_smoke_event.json
check "POST /event 帶 token severity=info -> 200 ok" $?

# 5. 最近啟動日誌無 traceback / 啟動失敗
if journalctl -u kiro-devops --since "10 minutes ago" --no-pager 2>/dev/null | \
    grep -qE "Traceback|CRITICAL|Failed to start"; then
    check "journalctl 最近 10 分鐘無 Traceback/CRITICAL" 1
else
    check "journalctl 最近 10 分鐘無 Traceback/CRITICAL" 0
fi

# 6. MULTI_PROFILE_ENABLED 確認為 false（legacy smoke 只在 legacy 模式有效）
if [ "${MULTI_PROFILE_ENABLED:-false}" = "true" ]; then
    echo "SKIP  目前為 multi-profile 模式，legacy smoke 不適用"
    exit 2
fi

if [ "$FAILED" -eq 0 ]; then
    echo "== legacy smoke test 全部通過 =="
else
    echo "== legacy smoke test 有失敗項目 =="
fi
exit "$FAILED"
```

- [ ] **步驟 2：建立祕密掃描腳本**

建立 `scripts/secret_leak_scan.sh`：

```bash
#!/bin/bash
# 掃描服務日誌與 Dashboard API response 是否洩漏 Secret 或 AWS credential（規格 §16、§22.12）
# 用法：bash scripts/secret_leak_scan.sh [分鐘數，預設 60]
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."
SINCE="${1:-60}"

if [ -f .env ]; then
    set -a; source .env; set +a
fi

FAILED=0
scan() {  # scan <名稱> <pattern>（grep -F 固定字串）
    local name="$1" pattern="$2"
    [ -z "$pattern" ] && return 0
    if journalctl -u kiro-devops --since "${SINCE} minutes ago" --no-pager 2>/dev/null \
        | grep -qF "$pattern"; then
        echo "FAIL  ${name} 出現在 journalctl"
        FAILED=1
    else
        echo "PASS  ${name} 未出現在 journalctl"
    fi
}

scan "FEISHU_APP_SECRET" "${FEISHU_APP_SECRET:-}"
scan "WEBHOOK_TOKEN" "${WEBHOOK_TOKEN:-}"
scan "DASHBOARD_TOKEN" "${DASHBOARD_TOKEN:-}"
scan "AWS_ACCESS_KEY_ID 值" "${AWS_ACCESS_KEY_ID:-}"
scan "AWS_SECRET_ACCESS_KEY 值" "${AWS_SECRET_ACCESS_KEY:-}"
scan "AWS_SESSION_TOKEN 值" "${AWS_SESSION_TOKEN:-}"

# 通用 credential 形態掃描（不限於 .env 值）
if journalctl -u kiro-devops --since "${SINCE} minutes ago" --no-pager 2>/dev/null \
    | grep -qE "AKIA[0-9A-Z]{16}|aws_secret_access_key\s*="; then
    echo "FAIL  日誌出現 AWS credential 形態"
    FAILED=1
else
    echo "PASS  日誌無 AWS credential 形態"
fi

# Dashboard response 掃描（需 DASHBOARD_TOKEN；未啟用則略過）
PORT="${WEBHOOK_PORT:-8080}"
if [ -n "${DASHBOARD_TOKEN:-}" ]; then
    BODY=$(curl -s --max-time 10 -H "X-Dashboard-Token: ${DASHBOARD_TOKEN}" \
        "http://127.0.0.1:${PORT}/dashboard/api/config" 2>/dev/null || true)
    LEAK=0
    for pattern in "${FEISHU_APP_SECRET:-}" "${WEBHOOK_TOKEN:-}" "${AWS_SECRET_ACCESS_KEY:-}"; do
        [ -n "$pattern" ] && echo "$BODY" | grep -qF "$pattern" && LEAK=1
    done
    if [ "$LEAK" -eq 1 ]; then
        echo "FAIL  Dashboard /api/config response 含 Secret"
        FAILED=1
    else
        echo "PASS  Dashboard /api/config response 無 Secret"
    fi
else
    echo "SKIP  DASHBOARD_TOKEN 未設定，略過 Dashboard response 掃描"
fi

exit "$FAILED"
```

Dashboard 認證標頭以計畫 4 完成後的實際機制為準（`require_auth`）；若使用 `Authorization: Bearer` 或 cookie，調整 `curl` 標頭即可，掃描邏輯不變。

- [ ] **步驟 3：語法與權限檢查**

```bash
bash -n scripts/legacy_smoke_test.sh && bash -n scripts/secret_leak_scan.sh
chmod +x scripts/legacy_smoke_test.sh scripts/secret_leak_scan.sh
shellcheck scripts/legacy_smoke_test.sh scripts/secret_leak_scan.sh 2>/dev/null || echo "shellcheck 未安裝，略過"
```

預期：`bash -n` 無輸出（語法正確）。

- [ ] **步驟 4：在現行 legacy 服務上實跑**

```bash
bash scripts/legacy_smoke_test.sh
bash scripts/secret_leak_scan.sh 60
```

預期：smoke test 全部 PASS、exit 0；掃描全部 PASS／SKIP、exit 0。若現行服務未運行，先以 `./start.sh` 或 `sudo systemctl start kiro-devops` 啟動後再執行。

- [ ] **步驟 5：提交任務 5**

```bash
git add scripts/legacy_smoke_test.sh scripts/secret_leak_scan.sh
git commit -m "feat(發布): 加入 legacy smoke test 與祕密洩漏掃描"
```

---

### 任務 6：Dark Deployment 執行（規格 §19.2）

**文件：**
- 不新增檔案；此任務為部署執行檢查清單，結果記錄於任務 11 的驗收文件。

前提：計畫 1–4 的程式已合併到待發布 commit。本任務把新程式部署上線但**保持功能關閉**，證明沒有回歸。

- [ ] **步驟 1：確認發布候選 commit 且工作區乾淨**

```bash
git status --porcelain
git log --oneline -1
```

預期：無輸出（乾淨）；記下發布候選 SHA，後續回滾演練（任務 9）以此為「新版本」。

- [ ] **步驟 2：建立升級前 release manifest**

```bash
python3 scripts/release_manifest.py
python3 scripts/release_manifest.py --verify "$(ls -dt runtime/release-backups/*/ | head -1)manifest.json"
```

預期：manifest 建立並 verify OK；`git.dirty` 為 `false`。此快照是任務 9 版本回滾演練的依據，必須保留。

- [ ] **步驟 3：部署新程式並確認 flag 關閉**

```bash
grep -E "^MULTI_PROFILE_ENABLED" .env || echo "MULTI_PROFILE_ENABLED=false" | tee -a /dev/null
grep -qE "^(export )?MULTI_PROFILE_ENABLED=true" .env && echo "錯誤：dark deployment 階段不得開啟" || echo "flag 關閉，可部署"
sudo systemctl restart kiro-devops
sleep 5
systemctl is-active kiro-devops
```

預期：輸出 `flag 關閉，可部署`；服務 `active`。

- [ ] **步驟 4：執行完整測試套件與編譯檢查**

```bash
pytest -q
python3 -m compileall -q multi_profile scripts tests
```

預期：`pytest` 0 failed；`compileall` exit 0。

- [ ] **步驟 5：執行 legacy smoke test 與祕密掃描**

```bash
bash scripts/legacy_smoke_test.sh
bash scripts/secret_leak_scan.sh 30
```

預期：全部 PASS，exit 0。

- [ ] **步驟 6：手動 legacy 回歸檢查清單（真實通道）**

- [ ] 飛書私聊傳送普通訊息，Bot 正常回覆（走舊 `MessageHandler` 路徑）。
- [ ] 飛書群 @Bot 傳送普通訊息，Bot 在原群回覆。
- [ ] 觸發一則測試告警（或 `scripts/monitor_alert_chain.sh` 配合 drain 節點），確認群告警分析完成且由原 App 回覆原群。
- [ ] `/sessions`、`/status` 等既有命令行為不變。
- [ ] Dashboard 既有頁面（Resources、Events、Scheduler）正常。
- [ ] `journalctl -u kiro-devops --since "30 minutes ago"` 無新錯誤。

任一項失敗：停止，依 systematic-debugging 定位；確認為新版本回歸時，執行任務 9 的同版本緊急回滾流程恢復舊版行為（此時新舊版本 flag 皆關閉，回滾等價於 `git checkout` 前一 release + 重啟）。

---

### 任務 7：離線設定與完整驗證（規格 §19.3）

**文件：**
- 不新增檔案；產生的 `multi_profile_config.yaml` 為部署期 artefact，已被 `.gitignore` 排除。

- [ ] **步驟 1：取得目前 AWS 身分 Account ID**

```bash
aws sts get-caller-identity --profile "${AWS_PROFILE:-default}" --query Account --output text
```

預期：輸出 12 位 Account ID，記為 `<ACCOUNT_ID>`。

- [ ] **步驟 2：產生 legacy-default Draft**

```bash
python3 scripts/build_legacy_default_config.py \
  --env-file .env \
  --account-id "<ACCOUNT_ID>" \
  --working-dir /home/ubuntu/kiro-devops \
  --output multi_profile_config.yaml
```

預期：產生 `multi_profile_config.yaml`；輸出不含 `FEISHU_APP_SECRET` 的值；所有 `FEISHU_POLL_CHAT_IDS` 群出現在 `routes` 且 `profile: legacy-default`。

- [ ] **步驟 3：比對既有群清單完整性**

```bash
python3 - <<'PY'
import yaml
with open("multi_profile_config.yaml", encoding="utf-8") as fh:
    config = yaml.safe_load(fh)
routes = {r["chat_id"] for r in config["routes"]}
env_chats = set()
import os
for line in open(".env", encoding="utf-8"):
    if line.startswith("FEISHU_POLL_CHAT_IDS="):
        env_chats = {c.strip() for c in line.split("=", 1)[1].strip().split(",") if c.strip()}
missing = env_chats - routes
extra = routes - env_chats
print("missing:", missing or "無")
print("extra:", extra or "無")
assert not missing, "有既有群未映射"
PY
```

預期：`missing: 無`。`extra` 若存在需人工確認來源（例如先前手動加入的群），確認後保留。

- [ ] **步驟 4：完整驗證（含 STS），不切換流量**

透過計畫 4 的 Dashboard `Multi Profile Config` 頁面匯入 Draft 並執行驗證，或以 API：

```bash
curl -s -X POST "http://127.0.0.1:${WEBHOOK_PORT:-8080}/dashboard/api/multi-profile/validate" \
  -H "X-Dashboard-Token: ${DASHBOARD_TOKEN}" \
  -H "Content-Type: application/yaml" \
  --data-binary @multi_profile_config.yaml | python3 -m json.tool
```

預期（對應規格 §13.3 驗證順序）：schema、env 引用、關聯、工作目錄、Agent／模型、AWS profile 存在、STS `get-caller-identity`、`expected_account_id` 核對全部通過；任何一步失敗都修正 Draft 後重試，不得跳過。驗證通過後**發布** Draft（此時 flag 仍為 false，發布只建立 snapshot 與 revision，不影響 legacy 流量）。

- [ ] **步驟 5：確認服務行為未變**

```bash
bash scripts/legacy_smoke_test.sh
journalctl -u kiro-devops --since "15 minutes ago" --no-pager | grep -i "multi-profile\|generation" || echo "無 multi-profile 活動（符合預期）"
```

預期：smoke test 通過；legacy 路徑完全不受已發布 snapshot 影響。

---

### 任務 8：切換與漸進擴展（規格 §19.4）

**文件：**
- 不新增檔案；此任務為上線執行檢查清單。

此任務是本系列計畫中**唯一允許**把 `MULTI_PROFILE_ENABLED` 設為 `true` 的地方（規格 §24）。

- [ ] **步驟 1：切換前快照**

```bash
python3 scripts/release_manifest.py
```

預期：建立切換前快照；若切換後需要回到「flag 關閉的新版本」，任務 9 流程以本次快照為準。

- [ ] **步驟 2：啟用 flag 並重啟**

```bash
sed -i -E 's|^(export )?MULTI_PROFILE_ENABLED=.*|MULTI_PROFILE_ENABLED=true|' .env
grep -q "^MULTI_PROFILE_ENABLED=" .env || echo "MULTI_PROFILE_ENABLED=true" >> .env
sudo systemctl restart kiro-devops
sleep 5
systemctl is-active kiro-devops
```

- [ ] **步驟 3：確認所有既有群仍走 legacy-default**

檢查清單：

- [ ] Dashboard 狀態頁顯示模式為 multi-profile、generation ≥ 1、`legacy-bot` App 為 `connected`。
- [ ] 每個既有群發送 `/profile`，回覆的 profile 別名為 `legacy-default`，Account ID 遮罩格式為 `********` + 最後 4 位。
- [ ] 任一既有群發送普通 @Bot 訊息，原 App 在原群回覆。
- [ ] 私聊既有 App，使用 `legacy-default`（App 的 `default_profile`）。
- [ ] 觸發測試告警，告警分析使用 `legacy-default` 的 AWS profile 並由原 App 回覆原群。

- [ ] **步驟 4：驗證 Account ID、Session 與記憶隔離**

- [ ] 日誌中 Kiro 任務記錄的 trace 含 app key、chat ID、profile 別名、generation（規格 §16）。
- [ ] 同時在兩個不同群（同一 `legacy-default`）發送訊息：兩者並行完成，各自建立獨立 Session（`runtime/tenant_sessions.db` 中 `principal_key` 不同）。
- [ ] 同一使用者在群 A 建立的 Session 內容，在群 B 用 `/sessions` 看不到。

- [ ] **步驟 5：逐群改綁實際 profile**

對每個要遷移的群：

- [ ] 在 Dashboard 建立目標 profile（新 AWS profile 需先通過 STS 與 `expected_account_id` 驗證）。
- [ ] 發布只變更該群路由的 Draft（熱載入，不需重啟）。
- [ ] 該群發送 `/profile` 確認新別名與遮罩 Account ID。
- [ ] 該群下一則訊息建立新 Session（fingerprint 改變，舊 Session 保留但不再恢復）。
- [ ] 觀察至少一個完整對話與（如適用）一次告警分析無異常後，才改綁下一群。

- [ ] **步驟 6：新 App 分批加入**

每批（建議每批 1–2 個 App）：

- [ ] `.env` 加入新 App 的 `FEISHU_<NAME>_APP_ID`／`FEISHU_<NAME>_APP_SECRET`。
- [ ] Dashboard 發布新增 App 與其群路由的 Draft；狀態顯示 `pending-restart`。
- [ ] 安排重啟：`sudo systemctl restart kiro-devops`。
- [ ] 確認新 App `connected`；新 App 的群發送 `/profile` 與普通訊息驗證路由、回覆 App 正確。
- [ ] 既有 App／群回歸：`bash scripts/legacy_smoke_test.sh` 中適用項目 + 任一既有群普通對話。
- [ ] 每批通過並觀察（建議 ≥ 24 小時）後才擴展下一批。

- [ ] **步驟 7：立即停止擴展並回滾的觸發條件（規格 §19.4）**

出現以下任一情況，立即停止擴展，保留現場日誌（`journalctl -u kiro-devops --since "-1h" > /tmp/incident.log`），並執行任務 9 的同版本緊急回滾：

- 任何任務使用了**錯誤 AWS Account**（`/profile` 遮罩或 STS 與預期不符）。
- 出現**跨群或跨 App Session**（一群看到另一群的對話）。
- 出現**跨群或跨 App 記憶**。
- 訊息由**錯誤 App 回覆**。
- 有效設定無法載入且**不能維持 last-known-good**。

以上同時屬於規格 §22 的 Critical 缺陷；排除前不得繼續擴展，也不得宣布 go。

---

### 任務 9：回滾演練（規格 §20.2、§20.3）

**文件：**
- 不新增檔案；演練計時與結果記錄於任務 11 的驗收文件。

正式發布前必須完成一次應用版本回滾演練，目標 **5 分鐘內**恢復舊單 App 服務。演練在暫存環境執行；若只能在生產主機執行，安排在維護窗口並先完成任務 6 的 manifest。

- [ ] **步驟 1：同版本緊急回滾演練（規格 §20.2）**

```bash
START=$SECONDS
sed -i -E 's|^(export )?MULTI_PROFILE_ENABLED=.*|MULTI_PROFILE_ENABLED=false|' .env
sudo systemctl restart kiro-devops
sleep 5
systemctl is-active kiro-devops
bash scripts/legacy_smoke_test.sh
echo "elapsed: $((SECONDS - START))s"
```

檢查清單：

- [ ] flag 關閉後服務使用原 `.env`、單 App 與舊 Session／記憶路徑。
- [ ] legacy health、訊息接收、原 App 回覆與 Kiro smoke test 全部通過。
- [ ] 舊 `user_sessions.json` 的 Session 在 legacy 模式下可恢復（新模式的 `tenant_sessions.db` 在 legacy 模式不可見，符合規格 §10.3）。
- [ ] 計時 ≤ 5 分鐘。

- [ ] **步驟 2：應用版本回滾演練（規格 §20.3）**

以任務 6 步驟 2 建立的升級前 manifest 為目標：

```bash
MANIFEST="<升級前快照>/manifest.json"
python3 scripts/rollback_to_release.py --manifest "$MANIFEST" --dry-run --no-systemctl
START=$SECONDS
python3 scripts/rollback_to_release.py --manifest "$MANIFEST" --yes
echo "elapsed: $((SECONDS - START))s"
```

檢查清單：

- [ ] dry-run 計畫中 `RESTORE` 不含任何 `.db`。
- [ ] 執行後 `git rev-parse HEAD` 等於 manifest 記錄的 commit。
- [ ] `.env` 已恢復且 `MULTI_PROFILE_ENABLED=false`。
- [ ] 所有目前 SQLite（`events.db`、`memory_db/*.db`、`runtime/tenant_sessions.db`）mtime 未被回滾工具改寫。
- [ ] `bash scripts/legacy_smoke_test.sh` 通過，舊單 App 服務可用。
- [ ] 總計時 ≤ 5 分鐘；超過則記錄瓶頸步驟並修正工具或流程後重演。

- [ ] **步驟 3：災難復原保護驗證（不實際覆寫）**

```bash
python3 scripts/rollback_to_release.py --manifest "$MANIFEST" --dry-run --no-systemctl --restore-sqlite
```

預期：非零退出，錯誤訊息要求 `--disaster-restore`；確認例行路徑無法誤觸 SQLite 覆寫。

- [ ] **步驟 4：演練後恢復到新版本**

```bash
git checkout <發布候選 SHA>
# 如需重新安裝依賴：venv/bin/pip install -r requirements.txt
sudo systemctl restart kiro-devops
bash scripts/legacy_smoke_test.sh
```

預期：回到新版本、flag 關閉狀態，smoke test 通過；之後才允許進入任務 10。

---

### 任務 10：端到端驗收（規格 §22 的 12 項）

**文件：**
- 不新增檔案；每項的證據（指令輸出、截圖、日誌摘錄）記入任務 11 的驗收文件。

逐項執行並記錄；任一項失敗即為 no-go。

- [ ] **驗收 1：完整 `pytest` 零失敗**

```bash
pytest -q
```

- [ ] **驗收 2：Python 編譯檢查通過**

```bash
python3 -m compileall -q . -x '(venv|\.git|__pycache__)'
```

- [ ] **驗收 3：Legacy 模式普通聊天與群告警 smoke test 通過**

```bash
bash scripts/legacy_smoke_test.sh
```

加上任務 6 步驟 6 的手動通道檢查（普通聊天 + 群告警）各一次。

- [ ] **驗收 4：至少 2 個唯讀或非生產 AWS profile 完成真實 STS 與 Kiro E2E**

- [ ] 準備第二個唯讀／非生產 AWS CLI profile（`~/.aws/config`），確認 `aws sts get-caller-identity --profile <name>` 非互動可用。
- [ ] 在 multi-profile 設定中新增第二個 profile（如 `sandbox-ro`）與一條測試群路由，發布並驗證通過。
- [ ] 兩個 profile 各自的群發送要求查詢 caller identity 的 Kiro 任務；回覆中的 Account ID 分別等於各 profile 的 `expected_account_id`。
- [ ] STS 驗證時間顯示於 Dashboard profile 狀態。

- [ ] **驗收 5：多群共用同一 profile 時 Session、記憶與任務仍完全隔離**

- [ ] 兩個群映射同一 profile，同一使用者分別對話：各自 Session 獨立，`/sessions` 互不見。
- [ ] 任務 4 的規模測試 `test_fifty_concurrent_messages_have_no_shared_state_pollution` 通過可作為自動化證據。
- [ ] 語義記憶查詢不跨群（以計畫 2 的 `tests/test_multi_profile_memory_isolation.py` 為證據，重跑一次）。

- [ ] **驗收 6：不同 profile 的並行任務取得正確 Account ID**

- [ ] 在兩個不同 profile 的群同時發送 caller identity 查詢；兩者並行完成且 Account ID 各自正確、不互換。

- [ ] **驗收 7：未映射群 fail-closed（兩條路徑，無子程序）**

- [ ] 未映射群 @Bot 普通訊息：原群收到未配置提示。
- [ ] 未映射群發送可辨識告警：原群收到未配置提示，不執行分析。
- [ ] 未映射群未 @Bot 的普通輪詢訊息：保持靜默。
- [ ] 三種情況日誌均無 Kiro／AWS 子程序啟動：

```bash
journalctl -u kiro-devops --since "-10 min" --no-pager | grep -c "kiro-cli" || echo "0 個子程序"
```

- [ ] **驗收 8：群告警由原 App 回覆且使用群綁定 AWS profile**

- [ ] 觸發測試告警；回覆出現在原 App 的原群；trace 日誌顯示使用該群 ExecutionContext 的 profile 與 Region。

- [ ] **驗收 9：無效熱載入不影響目前有效 snapshot**

- [ ] Dashboard 發布一份故意無效的 Draft（如錯誤 `expected_account_id`）：發布被拒，目前 generation 不變，既有群對話不受影響。
- [ ] 以檔案層面破壞 `multi_profile_config.yaml` 後觸發 reload：保留舊 snapshot，服務繼續可用，last-known-good 機制生效。

- [ ] **驗收 10：設定 revision 回滾成功**

- [ ] Dashboard 查看 diff，選擇前一 revision，重新驗證通過後回滾；回滾產生新 revision 且內容等於所選歷史版本；受影響群行為回到該版本。

- [ ] **驗收 11：應用版本回滾演練 5 分鐘內恢復**

- [ ] 任務 9 步驟 2 的計時記錄 ≤ 5 分鐘，且檢查清單全過。

- [ ] **驗收 12：日誌與 Dashboard response 不含 Secret 或 AWS credential**

```bash
bash scripts/secret_leak_scan.sh 1440
```

- [ ] 另人工抽查 Dashboard `Multi Profile Config` 頁面與 `/profile` 群回覆：Account ID 僅遮罩顯示，無 Secret、無完整 credential、無完整 prompt。

---

### 任務 11：Go/No-Go Gate 與驗收文件

**文件：**
- 建立（執行期 artefact）：`docs/superpowers/acceptance/2026-07-14-multi-profile-go-no-go.md`

- [ ] **步驟 1：彙整驗收文件**

建立 `docs/superpowers/acceptance/2026-07-14-multi-profile-go-no-go.md`，格式：

```markdown
# 多 Profile 功能 Go/No-Go 驗收記錄

- 日期：
- 發布候選 commit：
- 升級前 manifest：runtime/release-backups/<時間戳>/manifest.json
- 執行人：

## 規格 §22 驗收結果（12 項）

| # | 項目 | 結果 | 證據 |
|---|------|------|------|
| 1 | 完整 pytest 零失敗 | | |
| 2 | 編譯檢查 | | |
| 3 | Legacy smoke | | |
| 4 | 雙 AWS profile STS + Kiro E2E | | |
| 5 | 同 profile 多群隔離 | | |
| 6 | 並行任務 Account ID 正確 | | |
| 7 | 未映射群 fail-closed | | |
| 8 | 告警原 App 回覆 + 正確 profile | | |
| 9 | 無效熱載入安全 | | |
| 10 | revision 回滾 | | |
| 11 | 版本回滾演練 ≤ 5 分鐘（實測：__ 秒） | | |
| 12 | 祕密掃描 | | |

## Critical 缺陷確認

- [ ] 無錯誤 AWS Account
- [ ] 無跨群／跨 App Session
- [ ] 無跨群／跨 App 記憶
- [ ] 無錯誤 App 回覆

## 決議

- [ ] GO：MULTI_PROFILE_ENABLED=true 維持啟用，功能正式發布
- [ ] NO-GO：執行同版本緊急回滾（flag=false + 重啟），缺陷修復後重跑任務 8–11
```

- [ ] **步驟 2：最終完整驗證**

```bash
pytest -q
python3 -m compileall -q multi_profile scripts tests
bash scripts/legacy_smoke_test.sh || true   # multi-profile 模式下此腳本 exit 2 屬預期
bash scripts/secret_leak_scan.sh 1440
```

預期：pytest 0 failed；compileall exit 0；祕密掃描 exit 0。

- [ ] **步驟 3：確認程式範圍未越界**

```bash
PLAN5_BASE_SHA=$(cat .git/plan5-base-sha)
git diff "${PLAN5_BASE_SHA}"..HEAD --stat
git diff "${PLAN5_BASE_SHA}"..HEAD -- \
  gateway.py message_handler.py kiro_executor.py session_router.py \
  alert_analysis.py platform_dispatcher.py multi_profile adapters dashboard
```

預期：diff 只含 `scripts/` 與 `tests/` 新檔（及本計畫文件）；第二段對 runtime 程式碼沒有輸出。

- [ ] **步驟 4：Go 決議與提交**

12 項驗收全過且無 Critical 缺陷時，決議 GO；`MULTI_PROFILE_ENABLED=true` 自此成為正式狀態。NO-GO 時執行任務 9 的同版本緊急回滾並記錄原因。

```bash
git add docs/superpowers/acceptance/2026-07-14-multi-profile-go-no-go.md
git commit -m "docs(驗收): 多 profile 功能 go/no-go 驗收記錄"
```

---

## 完成標準

- `scripts/release_manifest.py` 產生含 git commit、pip freeze、systemd unit checksum 與全部備份 checksum 的 manifest；`--verify` 可偵測竄改；`.env` 備份權限 600。
- `scripts/rollback_to_release.py` 依 manifest 回滾程式、依賴與設定；例行路徑永不覆寫 SQLite；恢復的 `.env` 強制 `MULTI_PROFILE_ENABLED=false`。
- `scripts/build_legacy_default_config.py` 由 `.env` 產生可被計畫 1 loader 載入的 legacy-default Draft，不輸出 Secret 值。
- 規模測試以 Fake Adapter／Fake Runtime 覆蓋 10 App／20 profile／100 路由／50 並行，證明路由正確、無共享狀態污染、熱載入不阻塞既有任務。
- Dark deployment、離線設定、切換擴展、回滾演練的檢查清單全部執行並記錄。
- 規格 §22 的 12 項驗收全部通過；版本回滾演練 ≤ 5 分鐘。
- Go/No-Go 驗收文件完成簽核；只有 GO 之後 `MULTI_PROFILE_ENABLED=true` 才成為正式狀態。
- 本計畫未修改計畫 1–4 的任何 runtime 程式碼。

## 不在本計畫範圍

- 不修改 `multi_profile/`、`gateway.py`、Adapters 或 Dashboard 程式碼（缺陷修復另開計畫）。
- 不實作 Scheduler／Webhook／Dashboard AWS 資源查詢的 profile 路由（規格 §3 非目標）。
- 不匯入舊 `user_sessions.json` 或舊記憶到新 Session DB（規格 §10.3、§23）。
- 不為每個 profile 建立 worker 程序或獨立 systemd 實例。
- 不在 YAML、manifest、revision 或任何工具輸出中保存 Secret 值。
- 災難復原（SQLite 覆寫）只驗證工具的保護邏輯，不在本計畫實際執行。
