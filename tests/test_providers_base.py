import pytest
from dashboard.providers.base import Resource, MetricPoint, ResourceMetrics, BaseResourceProvider


def test_resource_unique_id():
    r = Resource(provider="aws", resource_type="ec2", region="cn-north-1", id="i-123", name="test", status="running")
    assert r.unique_id == "aws:ec2:cn-north-1:i-123"


def test_resource_defaults():
    r = Resource(provider="tencent", resource_type="cvm", region="ap-tokyo", id="ins-1", name="t", status="RUNNING")
    assert r.tags == {}
    assert r.meta == {}
    assert r.class_type is None


def test_metric_point_creation():
    from datetime import datetime, timezone
    ts = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    mp = MetricPoint(timestamp=ts, value=42.0)
    assert mp.timestamp == ts
    assert mp.value == 42.0


def test_resource_metrics_defaults():
    rm = ResourceMetrics(
        resource_id="aws:ec2:cn-north-1:i-123",
        metric_name="CPUUtilization",
        points_7d=[],
        points_30d=[],
    )
    assert rm.current is None
    assert rm.stats_7d is None
    assert rm.stats_30d is None
    assert rm.sparkline_7d == []


def test_base_provider_is_abstract():
    with pytest.raises(TypeError):
        BaseResourceProvider()
