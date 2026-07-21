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
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("build_legacy_default_config")

APP_KEY = "legacy-bot"
PROFILE_ID = "legacy-default"
ACCOUNT_ID_RE = re.compile(r"^\d{12}$")


def parse_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip().removeprefix("export ").strip()] = value.strip().strip('"').strip("'")
    return values


def _env_int(env: dict, parser: argparse.ArgumentParser, *keys: str, default: int) -> int:
    for key in keys:
        raw = env.get(key)
        if raw:
            try:
                return int(raw)
            except ValueError:
                parser.error(f"{key}={raw!r} 不是整數")
    return default


def build_draft(env: dict, account_id: str, working_dir: str, parser) -> dict:
    profile = {
        "enabled": True,
        "aws_profile": env.get("AWS_PROFILE") or "default",
        "expected_account_id": account_id,
        "working_dir": working_dir,
        "sync_timeout": _env_int(env, parser, "KIRO_SYNC_TIMEOUT", "KIRO_TIMEOUT", default=120),
        "async_timeout": _env_int(env, parser, "KIRO_ASYNC_TIMEOUT", default=1800),
        "alert_timeout": _env_int(env, parser, "ALERT_ANALYZE_TIMEOUT", default=300),
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


def validate_draft(draft: dict, env: dict) -> None:
    """以計畫 1 的 load_config 自我驗證；失敗拋出例外。"""
    from multi_profile import load_config

    environ = {**os.environ, **env}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8",
    ) as fh:
        yaml.safe_dump(draft, fh, sort_keys=False, allow_unicode=True)
        temp_path = fh.name
    try:
        load_config(temp_path, environ=environ)
    finally:
        os.unlink(temp_path)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a legacy-default multi-profile draft YAML from .env",
    )
    parser.add_argument("--env-file", default=".env", help="來源 .env 路徑（預設 .env）")
    parser.add_argument("--account-id", required=True,
                        help="12 位 AWS Account ID（先用 aws sts get-caller-identity 取得）")
    parser.add_argument("--working-dir", required=True,
                        help="legacy-default profile 的工作目錄（須為已存在的絕對路徑）")
    parser.add_argument("--output", default=None, help="輸出檔案（預設 stdout）")
    parser.add_argument("--force", action="store_true", help="允許覆寫已存在的 --output 檔案")
    parser.add_argument("--no-validate", action="store_true",
                        help="跳過以計畫 1 load_config 的自我驗證（不建議）")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args(argv)

    if not ACCOUNT_ID_RE.fullmatch(args.account_id):
        print(
            f"expected_account_id 必須是 12 位數字，收到: {args.account_id!r}",
            file=sys.stderr,
        )
        return 2

    env_path = Path(args.env_file)
    if not env_path.is_file():
        logger.error("env file 不存在: %s", env_path)
        return 1
    env = parse_env_file(env_path)

    arg_parser = argparse.ArgumentParser()
    draft = build_draft(env, args.account_id, args.working_dir, arg_parser)

    if not args.no_validate:
        try:
            validate_draft(draft, env)
        except Exception as exc:
            logger.error("產生的 Draft 未通過計畫 1 load_config 驗證: %s", exc)
            return 1
        logger.info("Draft 已通過 load_config 自我驗證")

    output = yaml.safe_dump(draft, sort_keys=False, allow_unicode=True)
    if args.output:
        output_path = Path(args.output)
        if output_path.exists() and not args.force:
            logger.error("輸出檔案已存在: %s（使用 --force 覆寫）", output_path)
            return 1
        output_path.write_text(output, encoding="utf-8")
        logger.info("Draft 已寫入 %s", output_path)
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
