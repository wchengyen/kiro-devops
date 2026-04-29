#!/usr/bin/env python3
"""Alert matcher core engine with rule evaluation and config hot-reloading."""

import os
import re
import threading
from typing import Any

from dashboard.config_store import ConfigStore


class AlertMatcher:
    """Rule matching engine for alert-to-action routing.

    Rules use nested structure:
        {
          "name": "...",
          "enabled": true,
          "match": {"alertname": "...", "severity": "...", "labels": {...}},
          "action": {"agent": "...", "tools": [...], "timeout": 300}
        }
    """

    _REGEX_HINTS = (".*", "|", "^", "$", "+", "?", "{", "}", "[", "]")

    def __init__(self, mappings: list[dict], defaults: dict | None = None):
        self._mappings = mappings
        self._defaults = defaults or {}

    @staticmethod
    def _extract_field(record: dict, field_name: str) -> Any:
        """Extract a field value from a record.

        For ``alertname``, derive from ``title``:
        - If title contains ``[xxx]``, return content inside brackets.
        - Otherwise return the first word.
        """
        if field_name == "alertname":
            title = record.get("title", "")
            if not title:
                return ""
            start = title.find("[")
            if start != -1:
                end = title.find("]", start)
                if end != -1:
                    return title[start + 1 : end]
            parts = title.split()
            return parts[0] if parts else ""
        return record.get(field_name)

    @classmethod
    def _is_regex(cls, value: str) -> bool:
        """Auto-detect whether a string value should be treated as a regex."""
        for hint in cls._REGEX_HINTS:
            if hint in value:
                return True
        return False

    @classmethod
    def _match_value(cls, rule_value: Any, record_value: Any) -> bool:
        """Compare a rule value against a record value.

        Supports:
        - List OR match
        - Regex match (auto-detected)
        - Exact string match
        """
        if isinstance(rule_value, list):
            return any(cls._match_value(v, record_value) for v in rule_value)

        if isinstance(rule_value, str):
            record_str = str(record_value) if record_value is not None else ""
            if cls._is_regex(rule_value):
                return re.search(rule_value, record_str) is not None
            return rule_value == record_str

        return rule_value == record_value

    def _match_rule(self, rule: dict, record: dict) -> bool:
        """Evaluate whether a single rule's match conditions satisfy the record."""
        match = rule.get("match", {})
        for key, rule_value in match.items():
            if key == "labels":
                record_labels = record.get("_raw_labels", {})
                if not isinstance(rule_value, dict):
                    return False
                for label_key, label_rule_value in rule_value.items():
                    if not self._match_value(
                        label_rule_value, record_labels.get(label_key)
                    ):
                        return False
            else:
                record_value = self._extract_field(record, key)
                if not self._match_value(rule_value, record_value):
                    return False
        return True

    def match(self, record: dict) -> dict:
        """Evaluate rules in array order and return merged actions for the first match."""
        for rule in self._mappings:
            if rule.get("enabled") is False:
                continue
            if self._match_rule(rule, record):
                action = rule.get("action", {})
                return {**self._defaults, **action}
        return dict(self._defaults)


class ConfigReloader:
    """Thread-safe config hot-reloader with mtime-based invalidation."""

    def __init__(self, store: ConfigStore):
        self._store = store
        self._lock = threading.Lock()
        self._matcher: AlertMatcher | None = None
        self._mtime: float = 0.0

    def get_matcher(self) -> AlertMatcher:
        """Return a cached AlertMatcher, reloading if the config file changed."""
        with self._lock:
            path = self._store.mappings_path
            current_mtime = 0.0
            if os.path.exists(path):
                current_mtime = os.path.getmtime(path)

            if self._matcher is None or current_mtime > self._mtime:
                data = self._store.load()
                mappings = data.get("mappings", [])
                defaults = data.get("alert_defaults", {})
                self._matcher = AlertMatcher(mappings, defaults)
                self._mtime = current_mtime

            return self._matcher
