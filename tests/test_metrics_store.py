import os
import sqlite3
import tempfile
from datetime import datetime
from unittest.mock import patch

import pytest

from dashboard.metrics_store import (
    MetricsStore,
    _raw_db_path,
    _aggregated_db_path,
)


def test_raw_db_path_format():
    path = _raw_db_path(2026, 4, base_dir="/tmp/metrics")
    assert path == "/tmp/metrics/raw_metrics_2026_04.db"


def test_aggregated_db_path():
    path = _aggregated_db_path(base_dir="/tmp/metrics")
    assert path == "/tmp/metrics/aggregated_metrics.db"


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmpdir:
        s = MetricsStore(base_dir=tmpdir)
        yield s
        s.close()


def test_write_and_query_hourly(store):
    records = [
        ("ec2:cn-north-1:i-123", "CPUUtilization", 1714113600, 12.5, "cn-north-1"),
        ("ec2:cn-north-1:i-123", "CPUUtilization", 1714117200, 15.2, "cn-north-1"),
        ("ec2:cn-north-1:i-456", "CPUUtilization", 1714113600, 8.0, "cn-north-1"),
    ]
    store.write_hourly(records)

    result = store.query_hourly("ec2:cn-north-1:i-123", "CPUUtilization", 1714113000, 1714118000)
    assert len(result) == 2
    assert result[0]["timestamp"] == 1714113600
    assert result[0]["value"] == 12.5
    assert result[1]["timestamp"] == 1714117200
    assert result[1]["value"] == 15.2


def test_downsample_and_query_daily(store):
    # Insert hourly data for 2026-04-25
    base = int(datetime(2026, 4, 25, 0, 0, 0).timestamp())
    records = [
        ("ec2:cn-north-1:i-123", "CPUUtilization", base + h * 3600, float(10 + h), "cn-north-1")
        for h in range(24)
    ]
    store.write_hourly(records)

    # Downsample
    count = store.downsample_month(2026, 4)
    assert count == 1

    # Query daily
    result = store.query_daily("ec2:cn-north-1:i-123", "CPUUtilization", "2026-04-25", "2026-04-25")
    assert len(result) == 1
    row = result[0]
    assert row["date"] == "2026-04-25"
    assert row["min_value"] == 10.0
    assert row["avg_value"] == 21.5  # average of 10..33
    assert row["max_value"] == 33.0
    assert row["p95_value"] == 32.0  # 95th percentile of 24 values


def test_query_history_routes_to_hourly(store):
    base = int(datetime(2026, 4, 25, 0, 0, 0).timestamp())
    store.write_hourly([
        ("ec2:cn-north-1:i-123", "CPUUtilization", base, 10.0, "cn-north-1"),
    ])
    with patch("dashboard.metrics_store.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 4, 25, 12, 0, 0)
        mock_dt.fromtimestamp = datetime.fromtimestamp
        mock_dt.strptime = datetime.strptime
        mock_dt.timedelta = __import__("datetime").timedelta
        result = store.query_history("ec2:cn-north-1:i-123", "CPUUtilization", "24h")
    assert result["granularity"] == "hourly"
    assert len(result["data"]) == 1


def test_query_history_routes_to_daily(store):
    base = int(datetime(2026, 4, 25, 0, 0, 0).timestamp())
    store.write_hourly([
        ("ec2:cn-north-1:i-123", "CPUUtilization", base + h * 3600, float(10 + h), "cn-north-1")
        for h in range(24)
    ])
    store.downsample_month(2026, 4)

    with patch("dashboard.metrics_store.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 4, 25, 12, 0, 0)
        mock_dt.fromtimestamp = datetime.fromtimestamp
        mock_dt.strptime = datetime.strptime
        mock_dt.timedelta = __import__("datetime").timedelta
        result = store.query_history("ec2:cn-north-1:i-123", "CPUUtilization", "180d")
    assert result["granularity"] == "daily"
    assert len(result["data"]) == 1


def test_write_hourly_upsert_updates_value(store):
    ts = 1714113600
    store.write_hourly([
        ("ec2:cn-north-1:i-123", "CPUUtilization", ts, 12.5, "cn-north-1"),
    ])
    store.write_hourly([
        ("ec2:cn-north-1:i-123", "CPUUtilization", ts, 99.9, "cn-north-1"),
    ])
    result = store.query_hourly("ec2:cn-north-1:i-123", "CPUUtilization", ts, ts)
    assert len(result) == 1
    assert result[0]["value"] == 99.9


def test_query_hourly_cross_month(store):
    # March 31, 2026 23:00
    march_ts = int(datetime(2026, 3, 31, 23, 0, 0).timestamp())
    # April 1, 2026 01:00
    april_ts = int(datetime(2026, 4, 1, 1, 0, 0).timestamp())
    store.write_hourly([
        ("ec2:cn-north-1:i-123", "CPUUtilization", march_ts, 10.0, "cn-north-1"),
        ("ec2:cn-north-1:i-123", "CPUUtilization", april_ts, 20.0, "cn-north-1"),
    ])
    result = store.query_hourly("ec2:cn-north-1:i-123", "CPUUtilization", march_ts, april_ts)
    assert len(result) == 2
    assert result[0]["value"] == 10.0
    assert result[1]["value"] == 20.0


def test_cleanup_old_daily(store):
    # Insert some daily aggregated data
    base = int(datetime(2026, 4, 25, 0, 0, 0).timestamp())
    store.write_hourly([
        ("ec2:cn-north-1:i-123", "CPUUtilization", base + h * 3600, float(10 + h), "cn-north-1")
        for h in range(24)
    ])
    store.downsample_month(2026, 4)

    # Patch datetime.now to simulate being 200 days later
    with patch("dashboard.metrics_store.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 11, 12, 0, 0, 0)
        mock_dt.timedelta = __import__("datetime").timedelta
        deleted = store.cleanup_old_daily(keep_days=180)

    assert deleted >= 1
    # Verify old data is gone
    result = store.query_daily("ec2:cn-north-1:i-123", "CPUUtilization", "2026-04-25", "2026-04-25")
    assert len(result) == 0


def test_downsample_returns_insert_count(store):
    base = int(datetime(2026, 4, 25, 0, 0, 0).timestamp())
    store.write_hourly([
        ("ec2:cn-north-1:i-123", "CPUUtilization", base + h * 3600, float(10 + h), "cn-north-1")
        for h in range(24)
    ])
    count = store.downsample_month(2026, 4)
    assert count == 1  # One day of data


def test_query_history_7d_routes_to_hourly(store):
    base = int(datetime(2026, 4, 25, 0, 0, 0).timestamp())
    store.write_hourly([
        ("ec2:cn-north-1:i-123", "CPUUtilization", base, 10.0, "cn-north-1"),
    ])
    with patch("dashboard.metrics_store.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 4, 25, 12, 0, 0)
        mock_dt.fromtimestamp = datetime.fromtimestamp
        mock_dt.strptime = datetime.strptime
        mock_dt.timedelta = __import__("datetime").timedelta
        result = store.query_history("ec2:cn-north-1:i-123", "CPUUtilization", "7d")
    assert result["granularity"] == "hourly"


def test_query_history_30d_routes_to_hourly(store):
    base = int(datetime(2026, 4, 25, 0, 0, 0).timestamp())
    store.write_hourly([
        ("ec2:cn-north-1:i-123", "CPUUtilization", base, 10.0, "cn-north-1"),
    ])
    with patch("dashboard.metrics_store.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 4, 25, 12, 0, 0)
        mock_dt.fromtimestamp = datetime.fromtimestamp
        mock_dt.strptime = datetime.strptime
        mock_dt.timedelta = __import__("datetime").timedelta
        result = store.query_history("ec2:cn-north-1:i-123", "CPUUtilization", "30d")
    assert result["granularity"] == "hourly"


def test_query_history_invalid_range_raises(store):
    with pytest.raises(ValueError, match="Unsupported range"):
        store.query_history("ec2:cn-north-1:i-123", "CPUUtilization", "1y")


def test_write_hourly_empty_records_is_noop(store):
    store.write_hourly([])
    result = store.query_hourly("ec2:cn-north-1:i-123", "CPUUtilization", 0, 3600)
    assert result == []


def test_query_history_stats_all_none_when_no_data(store):
    with patch("dashboard.metrics_store.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 4, 25, 12, 0, 0)
        mock_dt.fromtimestamp = datetime.fromtimestamp
        mock_dt.timedelta = __import__("datetime").timedelta
        result = store.query_history("ec2:cn-north-1:i-123", "CPUUtilization", "24h")
    assert result["stats"] == {"min": None, "avg": None, "p95": None, "max": None}
    assert result["data"] == []
