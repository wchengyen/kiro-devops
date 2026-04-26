import os
import sqlite3
import tempfile
from datetime import datetime

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

def test_write_and_query_hourly():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MetricsStore(base_dir=tmpdir)
        records = [
            ("ec2:cn-north-1:i-123", "cpu_utilization", 1714113600, 12.5, "cn-north-1"),
            ("ec2:cn-north-1:i-123", "cpu_utilization", 1714117200, 15.2, "cn-north-1"),
            ("ec2:cn-north-1:i-456", "cpu_utilization", 1714113600, 8.0, "cn-north-1"),
        ]
        store.write_hourly(records)

        result = store.query_hourly("ec2:cn-north-1:i-123", "cpu_utilization", 1714113000, 1714118000)
        assert len(result) == 2
        assert result[0]["timestamp"] == 1714113600
        assert result[0]["value"] == 12.5
        assert result[1]["timestamp"] == 1714117200
        assert result[1]["value"] == 15.2

        store.close()
