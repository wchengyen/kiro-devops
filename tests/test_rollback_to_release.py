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
