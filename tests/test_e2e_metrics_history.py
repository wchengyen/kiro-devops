#!/usr/bin/env python3
"""端到端验证：Resource Metrics History 完整数据流测试.

运行方式:
    PYTHONPATH=/home/ubuntu/kiro-devops pytest tests/test_e2e_metrics_history.py -v
"""

import os
import sys
import tempfile
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dashboard.metrics_store import MetricsStore


class TestMetricsStoreEndToEnd:
    """验证 MetricsStore 完整数据流：写入 -> 查询 -> 降采样 -> 范围查询."""

    def test_write_and_query_hourly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MetricsStore(base_dir=tmpdir)
            base = int(datetime(2026, 4, 20, 0, 0, 0).timestamp())
            records = [
                ("ec2:cn-north-1:i-test", "CPUUtilization", base + h * 3600, float(10 + h), "cn-north-1")
                for h in range(24)
            ]
            store.write_hourly(records)
            result = store.query_hourly("ec2:cn-north-1:i-test", "CPUUtilization", base, base + 23 * 3600)
            assert len(result) == 24
            assert result[0]["value"] == 10.0
            assert result[-1]["value"] == 33.0
            store.close()

    def test_downsample_produces_daily_aggregates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MetricsStore(base_dir=tmpdir)
            base = int(datetime(2026, 4, 20, 0, 0, 0).timestamp())
            store.write_hourly([
                ("ec2:cn-north-1:i-test", "CPUUtilization", base + h * 3600, float(10 + h), "cn-north-1")
                for h in range(24)
            ])
            count = store.downsample_month(2026, 4)
            assert count == 1

            daily = store.query_daily("ec2:cn-north-1:i-test", "CPUUtilization", "2026-04-20", "2026-04-20")
            assert len(daily) == 1
            row = daily[0]
            assert row["min_value"] == 10.0
            assert row["avg_value"] == 21.5
            assert row["p95_value"] == 32.0
            assert row["max_value"] == 33.0
            store.close()

    def test_query_history_routes_correctly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MetricsStore(base_dir=tmpdir)
            # 写入 3 天数据
            for day in range(3):
                base = int(datetime(2026, 4, 20 + day, 0, 0, 0).timestamp())
                store.write_hourly([
                    ("ec2:cn-north-1:i-test", "CPUUtilization", base + h * 3600, float(10 + h), "cn-north-1")
                    for h in range(24)
                ])
            store.downsample_month(2026, 4)

            # 24h -> hourly
            with patch("dashboard.metrics_store.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 4, 22, 12, 0, 0)
                mock_dt.fromtimestamp = datetime.fromtimestamp
                mock_dt.strptime = datetime.strptime
                mock_dt.timedelta = __import__("datetime").timedelta
                result = store.query_history("ec2:cn-north-1:i-test", "CPUUtilization", "24h")
            assert result["granularity"] == "hourly"
            assert len(result["data"]) > 0
            assert all("timestamp" in d and "value" in d for d in result["data"])
            assert set(result["stats"].keys()) == {"min", "avg", "p95", "max"}

            # 180d -> daily
            with patch("dashboard.metrics_store.datetime") as mock_dt:
                mock_dt.now.return_value = datetime(2026, 4, 22, 12, 0, 0)
                mock_dt.fromtimestamp = datetime.fromtimestamp
                mock_dt.strptime = datetime.strptime
                mock_dt.timedelta = __import__("datetime").timedelta
                result = store.query_history("ec2:cn-north-1:i-test", "CPUUtilization", "180d")
            assert result["granularity"] == "daily"
            assert len(result["data"]) == 3

            store.close()

    def test_monthly_raw_dbs_and_aggregated_db_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MetricsStore(base_dir=tmpdir)
            # 跨两个月写入
            for month, day in [(3, 15), (4, 10)]:
                base = int(datetime(2026, month, day, 0, 0, 0).timestamp())
                store.write_hourly([
                    ("ec2:cn-north-1:i-test", "CPUUtilization", base + h * 3600, float(10 + h), "cn-north-1")
                    for h in range(24)
                ])
            store.downsample_month(2026, 3)
            store.downsample_month(2026, 4)
            store.close()

            assert os.path.exists(os.path.join(tmpdir, "raw_metrics_2026_03.db"))
            assert os.path.exists(os.path.join(tmpdir, "raw_metrics_2026_04.db"))
            assert os.path.exists(os.path.join(tmpdir, "aggregated_metrics.db"))

            # 验证表结构
            for db_name, table in [
                ("raw_metrics_2026_03.db", "hourly_metrics"),
                ("aggregated_metrics.db", "daily_aggregated"),
            ]:
                conn = sqlite3.connect(os.path.join(tmpdir, db_name))
                tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                assert table in tables
                conn.close()


class TestApiRouteEndToEnd:
    """验证 API 路由注册和响应格式."""

    def test_history_api_returns_correct_shape(self):
        from flask import Flask
        from dashboard import dashboard_bp, _sessions
        import dashboard.api
        import dashboard

        original_token = dashboard.DASHBOARD_TOKEN
        dashboard.DASHBOARD_TOKEN = "test-e2e-token"
        _sessions.clear()

        app = Flask(__name__)
        app.register_blueprint(dashboard_bp)

        with app.test_client() as client:
            # 认证
            resp = client.post("/api/dashboard/auth", json={"token": "test-e2e-token"})
            assert resp.status_code == 200

            with patch("dashboard.api.MetricsStore") as MockStore:
                mock_store = MockStore.return_value
                mock_store.query_history.return_value = {
                    "resource_id": "ec2:cn-north-1:i-test",
                    "metric": "CPUUtilization",
                    "range": "24h",
                    "granularity": "hourly",
                    "data": [{"timestamp": 1714113600, "value": 15.5}],
                    "stats": {"min": 10.0, "avg": 15.5, "p95": 20.0, "max": 25.0},
                }

                resp = client.get("/api/dashboard/resources/ec2:cn-north-1:i-test/history?range=24h")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["ok"] is True
                assert data["granularity"] == "hourly"
                assert data["stats"]["avg"] == 15.5
                mock_store.close.assert_called_once()

        dashboard.DASHBOARD_TOKEN = original_token
        _sessions.clear()

    def test_invalid_range_returns_400(self):
        from flask import Flask
        from dashboard import dashboard_bp, _sessions
        import dashboard.api
        import dashboard

        original_token = dashboard.DASHBOARD_TOKEN
        dashboard.DASHBOARD_TOKEN = "test-e2e-token"
        _sessions.clear()

        app = Flask(__name__)
        app.register_blueprint(dashboard_bp)

        with app.test_client() as client:
            client.post("/api/dashboard/auth", json={"token": "test-e2e-token"})
            resp = client.get("/api/dashboard/resources/ec2:cn-north-1:i-test/history?range=1y")
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["ok"] is False

        dashboard.DASHBOARD_TOKEN = original_token
        _sessions.clear()


class TestSyncScriptEndToEnd:
    """验证同步脚本可用性."""

    def test_script_help_shows_all_options(self):
        import subprocess
        result = subprocess.run(
            ["python3", "scripts/sync_resource_metrics.py", "--help"],
            capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": os.getcwd()},
        )
        assert result.returncode == 0
        assert "--backfill" in result.stdout
        assert "--incremental" in result.stdout
        assert "--downsample" in result.stdout

    def test_script_is_executable(self):
        assert os.access("scripts/sync_resource_metrics.py", os.X_OK)

    def test_script_contains_cron_documentation(self):
        with open("scripts/sync_resource_metrics.py") as f:
            content = f.read()
        assert "Cron setup example" in content
        assert "--backfill" in content
