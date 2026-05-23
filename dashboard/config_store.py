#!/usr/bin/env python3
"""Config store for dashboard: reads/writes .env and alert-to-agent mappings."""

import json
import os

CONFIG_PATH = "dashboard_config.json"

CORE_KEYS = [
    "KIRO_AGENT",
    "ALERT_NOTIFY_USER_ID",
    "ALERT_AUTO_ANALYZE_SEVERITY",
    "WEBHOOK_TOKEN",
    "WEBHOOK_PORT",
    "WEBHOOK_HOST",
    "ENABLE_MEMORY",
    "GROUP_AT_ONLY",
    "GROUP_ALERT_LISTEN_ENABLED",
    "DEFAULT_MODEL",
    "BACKGROUND_MODEL",
]


class ConfigStore:
    def __init__(self, env_path: str = ".env", mappings_path: str = None):
        self.env_path = env_path
        self.mappings_path = mappings_path or CONFIG_PATH

    def _read_dashboard_config(self) -> dict:
        if not os.path.exists(self.mappings_path):
            return {}
        try:
            with open(self.mappings_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_dashboard_config(self, data: dict) -> None:
        with open(self.mappings_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _strip_export(self, key: str) -> tuple[str, bool]:
        """Remove 'export ' prefix if present. Returns (clean_key, had_export)."""
        key = key.strip()
        if key.startswith("export "):
            return key[7:].strip(), True
        return key, False

    def read_core_config(self) -> dict:
        """Read .env and return dict of CORE_KEYS values.

        Missing keys return empty string. Sensitive keys are NOT masked here.
        Supports both KEY=value and export KEY=value formats.
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
                        key, _ = self._strip_export(key)
                        if key in values:
                            values[key] = value.strip()
        return values

    def write_core_config(self, updates: dict) -> None:
        """Write updates back to .env, preserving existing lines, comments and export prefix."""
        lines = []
        existing_keys = set()
        export_prefixes: dict[str, bool] = {}  # key -> whether original used 'export'

        if os.path.exists(self.env_path):
            with open(self.env_path, "r", encoding="utf-8") as f:
                for line in f:
                    original_line = line
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        lines.append(original_line)
                        continue
                    if "=" in stripped:
                        raw_key, _, _ = stripped.partition("=")
                        raw_key = raw_key.strip()
                        key, had_export = self._strip_export(raw_key)
                        export_prefixes[key] = had_export
                        if key in updates:
                            prefix = "export " if had_export else ""
                            lines.append(f"{prefix}{key}={updates[key]}\n")
                            existing_keys.add(key)
                        else:
                            lines.append(original_line)
                    else:
                        lines.append(original_line)

        # Append any keys not already present
        for key, value in updates.items():
            if key not in existing_keys:
                prefix = "export " if export_prefixes.get(key, False) else ""
                lines.append(f"{prefix}{key}={value}\n")

        with open(self.env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    def read_mappings(self) -> list[dict]:
        """Read dashboard_config.json, return list under 'mappings' key.

        Return [] if file missing or malformed.
        """
        data = self._read_dashboard_config()
        return data.get("mappings", [])

    def write_mappings(self, mappings: list[dict]) -> None:
        """Write mappings to dashboard_config.json, preserving other keys."""
        data = self._read_dashboard_config()
        data["mappings"] = mappings
        self._write_dashboard_config(data)

    def read_service_rules(self) -> list[dict]:
        """Read service name inference rules from dashboard_config.json."""
        data = self._read_dashboard_config()
        return data.get("service_rules", [])

    def write_service_rules(self, rules: list[dict]) -> None:
        """Write service_rules to dashboard_config.json, preserving other keys."""
        data = self._read_dashboard_config()
        data["service_rules"] = rules
        self._write_dashboard_config(data)

    def read_pinned_resources(self) -> list[str]:
        data = self._read_dashboard_config()
        return data.get("pinned_resources", [])

    def write_pinned_resources(self, pins: list[str]) -> None:
        data = self._read_dashboard_config()
        data["pinned_resources"] = pins
        self._write_dashboard_config(data)

    def read_alert_defaults(self) -> dict:
        data = self._read_dashboard_config()
        return data.get("alert_defaults", {
            "agent": "ec2-alert-analyzer",
            "tools": ["execute_bash"],
            "timeout": 300
        })

    def write_alert_defaults(self, defaults: dict) -> None:
        data = self._read_dashboard_config()
        data["alert_defaults"] = defaults
        self._write_dashboard_config(data)

    @staticmethod
    def _migrate_config(cfg: dict) -> dict:
        if "providers" not in cfg and "regions" in cfg:
            old_regions = cfg.pop("regions", [])
            cfg["providers"] = {
                "aws": {"enabled": True, "regions": old_regions}
            }
        # Migrate pins to include provider prefix
        pins = cfg.get("pins", [])
        migrated_pins = []
        for pin in pins:
            if not pin.startswith("aws:") and not pin.startswith("tencent:"):
                migrated_pins.append(f"aws:{pin}")
            else:
                migrated_pins.append(pin)
        cfg["pins"] = migrated_pins
        return cfg

    def load(self) -> dict:
        """Load dashboard config, auto-migrating from old flat format if needed."""
        cfg = self._read_dashboard_config()
        cfg = self._migrate_config(cfg)
        return cfg

    def save(self, cfg: dict) -> None:
        """Save dashboard config in the new provider-aware format."""
        cfg = self._migrate_config(cfg)
        self._write_dashboard_config(cfg)
