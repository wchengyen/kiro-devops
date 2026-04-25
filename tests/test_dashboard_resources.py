#!/usr/bin/env python3
"""Tests for dashboard resources discovery and metrics."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from dashboard.resources import (
    Resource,
    discover_ec2,
    discover_rds,
    discover_all,
    get_cloudwatch_metrics,
    compute_stats,
    sparkline_from_points,
    get_all_resources_with_metrics,
)


@patch("boto3.client")
def test_discover_ec2_returns_instances(mock_client):
    mock_ec2 = MagicMock()
    mock_ec2.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-123",
                        "State": {"Name": "running"},
                        "InstanceType": "t3.micro",
                        "Tags": [{"Key": "Name", "Value": "test1"}],
                    }
                ]
            }
        ]
    }
    mock_ec2._client_config.region_name = "us-east-1"
    mock_client.return_value = mock_ec2

    result = discover_ec2()
    assert len(result) == 1
    assert result[0].id == "ec2:i-123"
    assert result[0].name == "test1"
    assert result[0].status == "running"
    assert result[0].meta["instance_type"] == "t3.micro"
    assert result[0].meta["region"] == "us-east-1"
    assert result[0].meta["os"] == "Linux/Unix"


@patch("boto3.client")
def test_discover_ec2_windows(mock_client):
    mock_ec2 = MagicMock()
    mock_ec2.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-win",
                        "State": {"Name": "running"},
                        "InstanceType": "t3.large",
                        "Platform": "windows",
                        "Tags": [],
                    }
                ]
            }
        ]
    }
    mock_ec2._client_config.region_name = "ap-northeast-1"
    mock_client.return_value = mock_ec2

    result = discover_ec2()
    assert result[0].meta["os"] == "Windows"
    assert result[0].meta["region"] == "ap-northeast-1"


@patch("boto3.client")
def test_discover_rds_returns_instances(mock_client):
    mock_rds = MagicMock()
    mock_rds.describe_db_instances.return_value = {
        "DBInstances": [
            {
                "DBInstanceIdentifier": "my-db",
                "DBInstanceStatus": "available",
                "Engine": "mysql",
                "DBInstanceClass": "db.t3.micro",
            }
        ]
    }
    mock_rds._client_config.region_name = "us-west-2"
    mock_client.return_value = mock_rds

    result = discover_rds()
    assert len(result) == 1
    assert result[0].id == "rds:my-db"
    assert result[0].status == "available"
    assert result[0].meta["engine"] == "mysql"
    assert result[0].meta["region"] == "us-west-2"
    assert result[0].meta["db_instance_class"] == "db.t3.micro"


@patch("boto3.client")
def test_discover_ec2_no_name_tag(mock_client):
    mock_ec2 = MagicMock()
    mock_ec2.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-456",
                        "State": {"Name": "stopped"},
                        "InstanceType": "t3.small",
                        "Tags": [],
                    }
                ]
            }
        ]
    }
    mock_ec2._client_config.region_name = ""
    mock_client.return_value = mock_ec2

    result = discover_ec2()
    assert result[0].name == "i-456"


@patch("boto3.client")
def test_get_cloudwatch_metrics_returns_hourly_points(mock_client):
    mock_cw = MagicMock()
    mock_cw.get_metric_statistics.return_value = {
        "Datapoints": [
            {"Timestamp": datetime(2026, 4, 18, 0, 0), "Average": 10.5, "Maximum": 15.0},
            {"Timestamp": datetime(2026, 4, 18, 1, 0), "Average": 20.0, "Maximum": 25.0},
        ]
    }
    mock_client.return_value = mock_cw

    result = get_cloudwatch_metrics("i-123", "AWS/EC2", "InstanceId", days=7)
    assert len(result) == 2
    assert result[0]["Average"] == 10.5
    assert result[0]["Maximum"] == 15.0


@patch("boto3.client")
def test_get_cloudwatch_metrics_returns_empty_without_boto3(mock_client):
    with patch.dict("sys.modules", {"boto3": None}):
        result = get_cloudwatch_metrics("i-123", "AWS/EC2", "InstanceId")
        assert result == []


def test_compute_stats():
    points = [
        {"Average": 10.0, "Maximum": 12.0},
        {"Average": 20.0, "Maximum": 22.0},
        {"Average": 30.0, "Maximum": 35.0},
        {"Average": 40.0, "Maximum": 45.0},
        {"Average": 50.0, "Maximum": 55.0},
        {"Average": 60.0, "Maximum": 65.0},
        {"Average": 70.0, "Maximum": 75.0},
        {"Average": 80.0, "Maximum": 85.0},
        {"Average": 90.0, "Maximum": 95.0},
        {"Average": 100.0, "Maximum": 105.0},
    ]
    stats = compute_stats(points)
    assert stats["avg"] == 55.0
    assert stats["max"] == 105.0
    # p95 of 10 sorted values: index 9 (0.95 * 10 = 9.5 -> int 9)
    assert stats["p95"] == 100.0


def test_compute_stats_empty():
    assert compute_stats([]) == {"avg": None, "p95": None, "max": None}


def test_sparkline_from_points():
    points = [
        {"Timestamp": datetime(2026, 4, 18, 0, 0), "Average": 10.0},
        {"Timestamp": datetime(2026, 4, 18, 1, 0), "Average": 20.0},
        {"Timestamp": datetime(2026, 4, 19, 0, 0), "Average": 30.0},
    ]
    result = sparkline_from_points(points)
    assert len(result) == 2
    assert result[0] == 15.0  # (10+20)/2
    assert result[1] == 30.0


def test_sparkline_from_points_empty():
    assert sparkline_from_points([]) == []


@patch("dashboard.resources.discover_all")
@patch("dashboard.resources.get_cloudwatch_metrics")
def test_get_all_resources_with_metrics(mock_cw, mock_discover):
    mock_discover.return_value = [
        Resource(
            id="ec2:i-123",
            type="ec2",
            name="test1",
            raw_id="i-123",
            status="running",
            meta={},
        )
    ]
    # Return same points for both 7d and 30d calls
    mock_cw.return_value = [
        {"Timestamp": datetime(2026, 4, 18, h, 0), "Average": float(h), "Maximum": float(h + 5)}
        for h in range(24)
    ]

    result = get_all_resources_with_metrics(refresh=True)
    assert len(result["resources"]) == 1
    r = result["resources"][0]
    assert len(r["sparkline"]) == 1  # 24 hourly points -> 1 daily
    assert r["stats_7d"]["avg"] is not None
    assert r["stats_7d"]["p95"] is not None
    assert r["stats_7d"]["max"] is not None
    assert r["stats_30d"]["avg"] is not None


@patch("dashboard.resources.discover_all")
@patch("dashboard.resources.get_cloudwatch_metrics")
def test_get_all_resources_uses_cache(mock_cw, mock_discover):
    mock_discover.return_value = [
        Resource(
            id="ec2:i-123",
            type="ec2",
            name="test1",
            raw_id="i-123",
            status="running",
            meta={},
        )
    ]
    mock_cw.return_value = [
        {"Timestamp": datetime(2026, 4, 18, h, 0), "Average": float(h), "Maximum": float(h + 5)}
        for h in range(24)
    ]

    result1 = get_all_resources_with_metrics(refresh=True)
    assert result1["resources"][0]["sparkline"] == [11.5]

    mock_discover.reset_mock()
    mock_cw.reset_mock()
    result2 = get_all_resources_with_metrics(refresh=False)
    assert result2["resources"][0]["sparkline"] == [11.5]
    mock_discover.assert_not_called()
    mock_cw.assert_not_called()
