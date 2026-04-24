#!/usr/bin/env python3
"""Config store for dashboard: reads/writes .env and alert-to-agent mappings."""

import json
import os

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


class ConfigStore:
    def __init__(self, env_path: str = ".env", mappings_path: str = "dashboard_config.json"):
        self.env_path = env_path
        self.mappings_path = mappings_path

    def read_core_config(self) -> dict:
        """Read .env and return dict of CORE_KEYS values.

        Missing keys return empty string. Sensitive keys are NOT masked here.
        """
        values = {key: "" for key in CORE_KEYS}
        if os.path.exists(self.env_path):
            with open(self.env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, value = line.partition("=")
                        key = key.strip()
                        if key in values:
                            values[key] = value.strip()
        return values

    def write_core_config(self, updates: dict) -> None:
        """Write updates back to .env, preserving existing lines and comments."""
        lines = []
        existing_keys = set()

        if os.path.exists(self.env_path):
            with open(self.env_path, "r", encoding="utf-8") as f:
                for line in f:
                    original_line = line
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        lines.append(original_line)
                        continue
                    if "=" in stripped:
                        key, _, _ = stripped.partition("=")
                        key = key.strip()
                        if key in updates:
                            lines.append(f"{key}={updates[key]}\n")
                            existing_keys.add(key)
                        else:
                            lines.append(original_line)
                    else:
                        lines.append(original_line)

        # Append any keys not already present
        for key, value in updates.items():
            if key not in existing_keys:
                lines.append(f"{key}={value}\n")

        with open(self.env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    def read_mappings(self) -> list[dict]:
        """Read dashboard_config.json, return list under 'mappings' key.

        Return [] if file missing or malformed.
        """
        if not os.path.exists(self.mappings_path):
            return []
        try:
            with open(self.mappings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "mappings" in data:
                return data["mappings"]
            return []
        except (json.JSONDecodeError, OSError):
            return []

    def write_mappings(self, mappings: list[dict]) -> None:
        """Write {"mappings": [...]} to dashboard_config.json."""
        with open(self.mappings_path, "w", encoding="utf-8") as f:
            json.dump({"mappings": mappings}, f, ensure_ascii=False, indent=2)
