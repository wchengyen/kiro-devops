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
