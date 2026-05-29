# tests/test_sync_resource_metrics.py
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from dashboard.resources import Resource
from dashboard.metrics_store import MetricsStore
from scripts.sync_resource_metrics import parse_args, run_backfill, run_incremental


def test_parse_args_backfill():
    args = parse_args(["--backfill"])
    assert args.backfill is True
    assert args.incremental is False


def test_parse_args_incremental():
    args = parse_args(["--incremental"])
    assert args.backfill is False
    assert args.incremental is True


def test_parse_args_downsample():
    args = parse_args(["--downsample", "2026", "3"])
    assert args.downsample == [2026, 3]


@patch("scripts.sync_resource_metrics.discover_all")
@patch("boto3.client")
def test_backfill_flow(mock_client, mock_discover):
    resource = Resource(
        id="ec2:us-east-1:i-123",
        type="ec2",
        name="test",
        raw_id="i-123",
        status="running",
        meta={"region": "us-east-1"},
    )
    mock_discover.return_value = [resource]

    mock_point_time = datetime(2026, 4, 25, 10, 0, 0, tzinfo=timezone.utc)
    mock_cw_response = {
        "Datapoints": [
            {"Timestamp": mock_point_time, "Average": 15.5},
        ]
    }
    mock_cw = MagicMock()
    mock_cw.get_metric_statistics.return_value = mock_cw_response
    mock_client.return_value = mock_cw

    with tempfile.TemporaryDirectory() as tmpdir:
        count = run_backfill(base_dir=tmpdir)
        assert count == 1

        store = MetricsStore(base_dir=tmpdir)
        ts = int(mock_point_time.timestamp())
        result = store.query_hourly("ec2:us-east-1:i-123", "CPUUtilization", ts, ts)
        assert len(result) == 1
        assert result[0]["value"] == 15.5
        store.close()


@patch("scripts.sync_resource_metrics.discover_all")
@patch("scripts.sync_resource_metrics.datetime")
@patch("boto3.client")
def test_run_incremental(mock_client, mock_datetime, mock_discover):
    mock_datetime.datetime.now.return_value = datetime(2026, 4, 26, 0, 0, 0, tzinfo=timezone.utc)
    mock_datetime.timedelta = timedelta
    mock_datetime.timezone = timezone

    resource = Resource(
        id="ec2:us-east-1:i-123",
        type="ec2",
        name="test",
        raw_id="i-123",
        status="running",
        meta={"region": "us-east-1"},
    )
    mock_discover.return_value = [resource]

    mock_point_time = datetime(2026, 4, 25, 10, 0, 0, tzinfo=timezone.utc)
    mock_cw_response = {
        "Datapoints": [
            {"Timestamp": mock_point_time, "Average": 15.5},
        ]
    }
    mock_cw = MagicMock()
    mock_cw.get_metric_statistics.return_value = mock_cw_response
    mock_client.return_value = mock_cw

    with tempfile.TemporaryDirectory() as tmpdir:
        count = run_incremental(base_dir=tmpdir)
        assert count >= 1

        store = MetricsStore(base_dir=tmpdir)
        ts = int(mock_point_time.timestamp())
        result = store.query_hourly("ec2:us-east-1:i-123", "CPUUtilization", ts, ts)
        assert len(result) == 1
        assert result[0]["value"] == 15.5
        store.close()
