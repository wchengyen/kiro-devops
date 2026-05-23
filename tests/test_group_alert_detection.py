#!/usr/bin/env python3
"""Tests for group message structured alert detection."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from message_handler import MessageHandler
from adapters.base import IncomingMessage


class TestParseStructuredAlert:
    """Test MessageHandler._parse_structured_alert"""

    @pytest.fixture
    def handler(self):
        dispatcher = MagicMock()
        return MessageHandler(dispatcher=dispatcher)

    def test_prometheus_style_chinese_kv(self, handler):
        text = (
            "告警名称：KubePodCrashLooping\n"
            "告警级别：critical\n"
            "命名空间：content\n"
            "pod：my-app-123\n"
            "描述：Pod is crash looping"
        )
        record = handler._parse_structured_alert(text)
        assert record is not None
        assert record["title"] == "KubePodCrashLooping"
        assert record["severity"] == "critical"
        assert record["source"] == "prometheus"
        assert record["description"] == "Pod is crash looping"
        assert "my-app-123" in record["entities"]
        assert record["_raw_labels"]["namespace"] == "content"
        assert record["_raw_labels"]["pod"] == "my-app-123"

    def test_english_kv(self, handler):
        text = (
            "alertname: CPUThrottlingHigh\n"
            "severity: high\n"
            "namespace: kube-system\n"
            "instance: 10.0.0.1:9100"
        )
        record = handler._parse_structured_alert(text)
        assert record is not None
        assert record["title"] == "CPUThrottlingHigh"
        assert record["severity"] == "high"
        assert "10.0.0.1:9100" in record["entities"]
        assert record["_raw_labels"]["namespace"] == "kube-system"

    def test_json_payload(self, handler):
        payload = {
            "title": "MemoryUsageHigh",
            "severity": "warning",
            "source": "prometheus",
            "description": "memory > 80%",
            "labels": {"namespace": "production", "pod": "api-1"},
        }
        record = handler._parse_structured_alert(json.dumps(payload))
        assert record is not None
        assert record["title"] == "MemoryUsageHigh"
        assert record["severity"] == "warning"
        assert record["_raw_labels"]["namespace"] == "production"

    def test_json_with_alertname_instead_of_title(self, handler):
        payload = {
            "alertname": "DiskFull",
            "severity": "critical",
        }
        record = handler._parse_structured_alert(json.dumps(payload))
        assert record is not None
        assert record["title"] == "DiskFull"

    def test_chinese_severity_mapping(self, handler):
        text = "告警名称：Test\n级别：紧急"
        record = handler._parse_structured_alert(text)
        assert record["severity"] == "critical"

        text = "告警名称：Test\n级别：严重"
        record = handler._parse_structured_alert(text)
        assert record["severity"] == "high"

        text = "告警名称：Test\n级别：警告"
        record = handler._parse_structured_alert(text)
        assert record["severity"] == "warning"

    def test_non_alert_text_returns_none(self, handler):
        texts = [
            "大家好，今天开周会",
            "请问谁负责这个模块？",
            "@机器人 帮我查一下日志",
            "severity is high but no title",
        ]
        for text in texts:
            assert handler._parse_structured_alert(text) is None

    def test_title_fallback_from_alertname(self, handler):
        text = "alertname: NodeNotReady\nseverity: high"
        record = handler._parse_structured_alert(text)
        assert record["title"] == "NodeNotReady"

    def test_title_override(self, handler):
        text = "alertname: X\ntitle: Custom Title\nseverity: low"
        record = handler._parse_structured_alert(text)
        assert record["title"] == "Custom Title"

    def test_invalid_severity_fallback(self, handler):
        text = "告警名称：Test\n级别：未知级别"
        record = handler._parse_structured_alert(text)
        assert record["severity"] == "medium"

    def test_empty_text(self, handler):
        assert handler._parse_structured_alert("") is None
        assert handler._parse_structured_alert("   ") is None


class TestGroupAlertFlow:
    """Test MessageHandler.handle() group alert flow."""

    @pytest.fixture
    def handler(self):
        dispatcher = MagicMock()
        return MessageHandler(dispatcher=dispatcher)

    @pytest.fixture
    def group_message(self):
        return IncomingMessage(
            platform="feishu",
            raw_user_id="u1",
            unified_user_id="feishu:u1",
            message_id="msg1",
            text="告警名称：X\n告警级别：critical",
            chat_type="group",
            is_at_me=False,
        )

    @pytest.fixture
    def group_at_message(self):
        return IncomingMessage(
            platform="feishu",
            raw_user_id="u1",
            unified_user_id="feishu:u1",
            message_id="msg2",
            text="@机器人 帮我查日志",
            chat_type="group",
            is_at_me=True,
        )

    @patch("message_handler._GROUP_ALERT_LISTEN_ENABLED", True)
    def test_detects_alert_without_at(self, handler, group_message):
        with patch.object(handler, "_reply") as mock_reply:
            with patch.object(handler, "_trigger_group_alert_analysis"):
                handler.handle(group_message)
                # 应回复"正在分析"
                assert mock_reply.call_count == 1
                assert "检测到告警" in mock_reply.call_args[0][1]

    @patch("message_handler._GROUP_ALERT_LISTEN_ENABLED", True)
    def test_low_severity_alert_ignored(self, handler, group_message):
        group_message.text = "告警名称：X\n告警级别：low"
        with patch.object(handler, "_reply") as mock_reply:
            handler.handle(group_message)
            # 低级别告警静默忽略，不回复
            assert mock_reply.call_count == 0

    @patch("message_handler._GROUP_ALERT_LISTEN_ENABLED", False)
    def test_disabled_listening_ignores_group_without_at(self, handler, group_message):
        with patch.object(handler, "_reply") as mock_reply:
            handler.handle(group_message)
            # 未开启监听且未 @，不回复
            assert mock_reply.call_count == 0

    @patch("message_handler._GROUP_ALERT_LISTEN_ENABLED", True)
    def test_normal_chat_with_at_goes_through(self, handler, group_at_message):
        with patch.object(handler.kiro_executor, "execute") as mock_exec:
            with patch.object(handler, "_reply"):
                handler.handle(group_at_message)
                # @ 机器人的普通消息应进入 kiro 执行流程
                assert mock_exec.call_count == 1

    @patch("message_handler._GROUP_ALERT_LISTEN_ENABLED", True)
    def test_alert_in_at_message_also_detected(self, handler):
        msg = IncomingMessage(
            platform="feishu",
            raw_user_id="u1",
            unified_user_id="feishu:u1",
            message_id="msg3",
            text="告警名称：X\n告警级别：critical",
            chat_type="group",
            is_at_me=True,
        )
        with patch.object(handler, "_reply") as mock_reply:
            with patch.object(handler, "_trigger_group_alert_analysis"):
                handler.handle(msg)
                # 即使 @ 了，告警检测优先
                assert mock_reply.call_count == 1
                assert "检测到告警" in mock_reply.call_args[0][1]
