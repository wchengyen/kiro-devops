#!/usr/bin/env python3
"""Tests for alert_matcher core engine."""

import json
import os
import time

import pytest

from alert_matcher import AlertMatcher, ConfigReloader
from dashboard.config_store import ConfigStore


class TestAlertMatcher:
    def test_exact_match(self):
        matcher = AlertMatcher(
            [{"match": {"alertname": "CPUHigh"}, "action": {"agent": "infra-agent"}}],
            defaults={"skill": "default-skill"},
        )
        record = {"title": "[CPUHigh] alert", "severity": "high"}
        result = matcher.match(record)
        assert result == {"skill": "default-skill", "agent": "infra-agent"}

    def test_fallback_when_no_match(self):
        matcher = AlertMatcher([], defaults={"agent": "default-agent"})
        record = {"title": "CPUHigh alert"}
        result = matcher.match(record)
        assert result == {"agent": "default-agent"}

    def test_no_defaults(self):
        matcher = AlertMatcher([])
        record = {"title": "CPUHigh alert"}
        result = matcher.match(record)
        assert result == {}

    def test_regex_match(self):
        matcher = AlertMatcher(
            [{"match": {"alertname": "Node.*"}, "action": {"agent": "node-agent"}}]
        )
        record = {"title": "[NodeExporterDown] ..."}
        result = matcher.match(record)
        assert result == {"agent": "node-agent"}

    def test_regex_does_not_match(self):
        matcher = AlertMatcher(
            [{"match": {"alertname": "Node.*"}, "action": {"agent": "node-agent"}}]
        )
        record = {"title": "[PodExporterDown] ..."}
        result = matcher.match(record)
        assert result == {}

    def test_array_or_match(self):
        matcher = AlertMatcher(
            [
                {
                    "match": {"severity": ["critical", "high"]},
                    "action": {"agent": "infra-agent"},
                }
            ]
        )
        record = {"title": "X", "severity": "high"}
        result = matcher.match(record)
        assert result == {"agent": "infra-agent"}

    def test_array_or_no_match(self):
        matcher = AlertMatcher(
            [
                {
                    "match": {"severity": ["critical", "high"]},
                    "action": {"agent": "infra-agent"},
                }
            ]
        )
        record = {"title": "X", "severity": "low"}
        result = matcher.match(record)
        assert result == {}

    def test_labels_match(self):
        matcher = AlertMatcher(
            [
                {
                    "match": {"labels": {"job": "node-exporter"}},
                    "action": {"agent": "node-agent"},
                }
            ]
        )
        record = {"title": "X", "_raw_labels": {"job": "node-exporter"}}
        result = matcher.match(record)
        assert result == {"agent": "node-agent"}

    def test_labels_no_match(self):
        matcher = AlertMatcher(
            [
                {
                    "match": {"labels": {"job": "node-exporter"}},
                    "action": {"agent": "node-agent"},
                }
            ]
        )
        record = {"title": "X", "_raw_labels": {"job": "kubelet"}}
        result = matcher.match(record)
        assert result == {}

    def test_disabled_rule_skipped(self):
        matcher = AlertMatcher(
            [
                {
                    "enabled": False,
                    "match": {"alertname": "NodeNotReady"},
                    "action": {"agent": "should-not-match"},
                }
            ],
            defaults={"agent": "default"},
        )
        record = {"title": "[NodeNotReady] ..."}
        result = matcher.match(record)
        assert result == {"agent": "default"}

    def test_priority_order(self):
        matcher = AlertMatcher(
            [
                {
                    "match": {"alertname": "Node.*"},
                    "action": {"agent": "first"},
                },
                {
                    "match": {"alertname": "NodeNotReady"},
                    "action": {"agent": "second"},
                },
            ],
            defaults={"agent": "default"},
        )
        record = {"title": "[NodeNotReady] ..."}
        result = matcher.match(record)
        assert result == {"agent": "first"}

    def test_action_overrides_defaults(self):
        matcher = AlertMatcher(
            [
                {
                    "match": {"alertname": "CPUHigh"},
                    "action": {
                        "agent": "custom-agent",
                        "tools": ["execute_bash", "grep"],
                        "timeout": 60,
                    },
                }
            ],
            defaults={
                "agent": "default-agent",
                "tools": ["execute_bash"],
                "timeout": 300,
            },
        )
        record = {"title": "[CPUHigh] ..."}
        result = matcher.match(record)
        assert result["agent"] == "custom-agent"
        assert result["tools"] == ["execute_bash", "grep"]
        assert result["timeout"] == 60

    def test_source_match(self):
        matcher = AlertMatcher(
            [
                {
                    "match": {"source": "prometheus", "alertname": "NodeNotReady"},
                    "action": {"agent": "node-agent"},
                }
            ]
        )
        assert matcher.match({"source": "prometheus", "title": "[NodeNotReady] ..."}) == {
            "agent": "node-agent"
        }
        assert matcher.match({"source": "cloudwatch", "title": "[NodeNotReady] ..."}) == {}

    def test_alertname_from_title_no_brackets(self):
        matcher = AlertMatcher(
            [{"match": {"alertname": "CPUHigh"}, "action": {"agent": "cpu-agent"}}]
        )
        record = {"title": "CPUHigh usage exceeded"}
        result = matcher.match(record)
        assert result == {"agent": "cpu-agent"}


class TestConfigReloader:
    def test_reloads_on_mtime_change(self, tmp_path):
        config_path = tmp_path / "dashboard_config.json"
        config_path.write_text(
            json.dumps(
                {
                    "mappings": [
                        {
                            "match": {"alertname": "Test"},
                            "action": {"agent": "test-agent"},
                        }
                    ],
                    "alert_defaults": {"agent": "default"},
                }
            )
        )
        store = ConfigStore(env_path=".env", mappings_path=str(config_path))
        reloader = ConfigReloader(store)

        matcher = reloader.get_matcher()
        result = matcher.match({"title": "[Test] ..."})
        assert result["agent"] == "test-agent"

        # Modify file
        time.sleep(0.1)
        config_path.write_text(
            json.dumps(
                {
                    "mappings": [
                        {
                            "match": {"alertname": "Test"},
                            "action": {"agent": "updated-agent"},
                        }
                    ],
                    "alert_defaults": {"agent": "default"},
                }
            )
        )

        matcher2 = reloader.get_matcher()
        result2 = matcher2.match({"title": "[Test] ..."})
        assert result2["agent"] == "updated-agent"

    def test_caches_when_unchanged(self, tmp_path):
        config_path = tmp_path / "dashboard_config.json"
        config_path.write_text(
            json.dumps(
                {
                    "mappings": [],
                    "alert_defaults": {"agent": "default"},
                }
            )
        )
        store = ConfigStore(env_path=".env", mappings_path=str(config_path))
        reloader = ConfigReloader(store)

        matcher1 = reloader.get_matcher()
        matcher2 = reloader.get_matcher()
        assert matcher1 is matcher2
