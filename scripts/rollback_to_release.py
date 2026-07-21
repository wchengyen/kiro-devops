#!/usr/bin/env python3
"""Roll back kiro-devops to the state recorded in a release manifest.

例行版本回滾（規格 20.3）：
    python3 scripts/rollback_to_release.py --manifest <snapshot>/manifest.json --yes

規則：
- 例行回滾只恢復程式（git checkout）、依賴、.env、dashboard_config.json 與 systemd unit。
- 永不覆寫任何目前 SQLite 檔（events.db、memory_db/*.db、runtime/tenant_sessions.db）。
- 升級前 SQLite 備份僅供災難復原，需同時給 --restore-sqlite --disaster-restore，
  且執行前先把目前 SQLite 另行複製到 runtime/disaster-restore-backup-<時間戳>/。
- 恢復的 .env 一律強制 MULTI_PROFILE_ENABLED=false。
"""
import argparse
import datetime
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("rollback_to_release")

FORCED_FLAG = "MULTI_PROFILE_ENABLED=false"
RESTORABLE_FILES = (".env", "dashboard_config.json", "user_sessions.json")
SQLITE_GLOBS = ("*.db", "memory_db/*.db", "runtime/*.db")


def build_plan(manifest: dict, args) -> list[tuple[str, str]]:
    """回傳有序 (action, detail)；dry-run 只輸出，不執行。"""
    snapshot_dir = Path(args.manifest).parent
    project_dir = Path(args.project_dir)
    plan = [] if args.no_systemctl else [("STOP", "sudo systemctl stop kiro-devops")]
    plan.append(("GIT", f"git checkout {manifest['git']['commit']}"))
    freeze_file = snapshot_dir / "requirements-frozen.txt"
    plan.append(("WRITE", f"{freeze_file}（manifest pip_freeze）"))
    plan.append(("PIP", f"{sys.executable} -m pip install -r {freeze_file}"))
    for entry in manifest["backups"]:
        if entry["kind"] == "sqlite":
            if args.restore_sqlite:
                plan.append(("RESTORE", f"[災難復原] {entry['backup']} -> {project_dir / entry['source']}"))
            else:
                plan.append(("SKIP", f"略過 SQLite 備份 {entry['source']}（例行回滾不覆寫資料檔）"))
        elif entry["source"] in RESTORABLE_FILES:
            plan.append(("RESTORE", f"{entry['backup']} -> {project_dir / entry['source']}"))
    plan.append(("ENV", f"強制 {FORCED_FLAG}"))
    if manifest["systemd"].get("unit_sha256"):
        plan.append(("RESTORE", "systemd unit + daemon-reload"))
    if not args.no_systemctl:
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


def current_sqlite_files(project_dir: Path) -> list[Path]:
    files = []
    for pattern in SQLITE_GLOBS:
        files.extend(path for path in sorted(project_dir.glob(pattern)) if path.is_file())
    return files


def disaster_restore(manifest: dict, snapshot_dir: Path, project_dir: Path, dry_run: bool) -> None:
    """僅在 --restore-sqlite --disaster-restore 同時給予時執行。"""
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safety_dir = project_dir / "runtime" / f"disaster-restore-backup-{timestamp}"
    logger.warning("災難復原：先把目前 SQLite 複製到 %s", safety_dir)
    for entry in manifest["backups"]:
        if entry["kind"] != "sqlite":
            continue
        source_backup = snapshot_dir / entry["backup"]
        target = project_dir / entry["source"]
        current = project_dir / entry["source"]
        if dry_run:
            logger.info("[dry-run] 備份目前 %s 並還原 %s", current, source_backup)
            continue
        if current.is_file():
            safety_dest = safety_dir / entry["source"].replace("/", "__")
            safety_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current, safety_dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_backup, target)
        logger.info("restored sqlite %s", entry["source"])


def execute_plan(manifest: dict, args) -> None:
    snapshot_dir = Path(args.manifest).parent
    project_dir = Path(args.project_dir)

    if not args.no_systemctl:
        run(["sudo", "systemctl", "stop", "kiro-devops"], dry_run=args.dry_run)

    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=project_dir, capture_output=True, text=True,
    )
    if dirty.returncode == 0 and dirty.stdout.strip():
        logger.error(
            "工作區 dirty，中止。請先 git stash 或提交後再執行回滾：\n%s", dirty.stdout,
        )
        raise SystemExit(1)

    run(["git", "checkout", manifest["git"]["commit"]], cwd=project_dir, dry_run=args.dry_run)

    freeze_file = snapshot_dir / "requirements-frozen.txt"
    if not args.dry_run:
        freeze_file.write_text(manifest["dependencies"]["pip_freeze"], encoding="utf-8")
    run([sys.executable, "-m", "pip", "install", "-r", str(freeze_file)], dry_run=args.dry_run)

    for entry in manifest["backups"]:
        if entry["kind"] == "sqlite":
            continue  # 例行路徑永不覆寫 SQLite
        if entry["source"] not in RESTORABLE_FILES:
            continue
        source_backup = snapshot_dir / entry["backup"]
        target = project_dir / entry["source"]
        logger.info("restore %s -> %s", source_backup, target)
        if not args.dry_run:
            shutil.copy2(source_backup, target)

    env_path = project_dir / ".env"
    if env_path.is_file() or not args.dry_run:
        logger.info("強制 %s", FORCED_FLAG)
        if not args.dry_run:
            force_flag_disabled(env_path)

    if args.restore_sqlite:
        disaster_restore(manifest, snapshot_dir, project_dir, args.dry_run)

    unit_sha256 = manifest["systemd"].get("unit_sha256")
    unit_path = manifest["systemd"].get("unit_path")
    if unit_sha256 and unit_path and Path(unit_path).is_file() and not args.dry_run:
        logger.warning(
            "systemd unit 內容未備份（manifest 只記錄 checksum）；"
            "如 %s 與升級前不同請人工還原，然後 sudo systemctl daemon-reload", unit_path,
        )

    if not args.no_systemctl:
        run(["sudo", "systemctl", "start", "kiro-devops"], dry_run=args.dry_run)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Roll back kiro-devops to a release manifest snapshot")
    parser.add_argument("--manifest", required=True, help="release manifest.json 路徑")
    parser.add_argument("--project-dir", default=None,
                        help="專案根目錄（預設 manifest 記錄的 project_dir）")
    parser.add_argument("--dry-run", action="store_true", help="只印出回滾計畫，不執行任何變更")
    parser.add_argument("--yes", action="store_true", help="跳過互動確認")
    parser.add_argument("--no-systemctl", action="store_true", help="不執行 systemctl stop/start")
    parser.add_argument("--restore-sqlite", action="store_true",
                        help="災難復原：還原升級前 SQLite 備份（需搭配 --disaster-restore）")
    parser.add_argument("--disaster-restore", action="store_true",
                        help="確認執行災難復原（會先備份目前 SQLite）")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args(argv)

    if args.restore_sqlite and not args.disaster_restore:
        logger.error("--restore-sqlite 需同時給予 --disaster-restore 才會執行（災難復原保護）")
        return 1

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if args.project_dir is None:
        args.project_dir = manifest.get("project_dir") or "."

    plan = build_plan(manifest, args)
    print("== 回滾計畫 ==")
    for action, detail in plan:
        print(f"{action:8s}{detail}")

    if args.dry_run:
        print("== dry-run：未執行任何變更 ==")
        return 0

    if not args.yes:
        answer = input("確認執行以上回滾計畫？[y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            logger.info("已取消")
            return 1

    execute_plan(manifest, args)
    logger.info("回滾完成。後續手動步驟：")
    logger.info("  1. bash scripts/legacy_smoke_test.sh")
    logger.info("  2. bash scripts/secret_leak_scan.sh 60")
    return 0


if __name__ == "__main__":
    sys.exit(main())
