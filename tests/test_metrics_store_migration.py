import sqlite3
import pytest
from datetime import datetime
from dashboard.metrics_store import MetricsStore


def test_provider_column_added(tmp_path):
    db_path = tmp_path / "raw_metrics_2026_04.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE hourly_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource_id TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            value REAL NOT NULL,
            region TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now')),
            UNIQUE(resource_id, metric_name, timestamp)
        )
        """
    )
    conn.commit()
    conn.close()

    store = MetricsStore(str(db_path))
    conn = sqlite3.connect(str(db_path))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(hourly_metrics)")]
    assert "provider" in cols
    conn.close()


def test_write_and_query_with_provider(tmp_path):
    db_path = tmp_path / "raw_metrics_2026_05.db"
    store = MetricsStore(str(db_path))
    # 使用当前时间附近的时间戳，确保落在 24h 查询范围内
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    store.write_raw(
        provider="tencent",
        timestamp=ts,
        resource_id="tencent:cvm:ap-tokyo:ins-1",
        metric="cpu_utilization",
        value=15.5,
    )
    result = store.query_history(
        "tencent:cvm:ap-tokyo:ins-1",
        "cpu_utilization",
        "24h",
    )
    assert result["granularity"] == "hourly"
    assert len(result["data"]) == 1
    assert result["data"][0]["value"] == 15.5
