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


def _git(project_dir: Path, *args) -> str | None:
    result = subprocess.run(
        ["git", *args], cwd=project_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def git_info(project_dir: Path) -> dict:
    commit = _git(project_dir, "rev-parse", "HEAD")
    if commit is None:
        # 非 git 目錄（例如測試沙箱）不阻擋快照，但欄位記為 null 並警告
        logger.warning("not a git repository: %s（git 欄位記為 null）", project_dir)
        return {"commit": None, "branch": None, "dirty": None}
    branch = _git(project_dir, "rev-parse", "--abbrev-ref", "HEAD")
    dirty = bool(_git(project_dir, "status", "--porcelain"))
    if dirty:
        logger.warning("git working tree is dirty；發布前檢查清單要求 dirty 為 false")
    return {"commit": commit, "branch": branch, "dirty": dirty}


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
