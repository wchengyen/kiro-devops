import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from dashboard.providers.aws import AWSProvider
from dashboard.providers.base import Resource


@pytest.fixture
def provider():
    with patch("dashboard.providers.aws._load_config") as m:
        m.return_value = {"providers": {"aws": {"enabled": True, "regions": ["cn-north-1"]}}}
        yield AWSProvider()


def test_name(provider):
    assert provider.name == "aws"


def test_is_enabled(provider):
    assert provider.is_enabled() is True


def test_regions(provider):
    assert provider.regions() == ["cn-north-1"]


def test_resource_types(provider):
    assert set(provider.resource_types()) == {"ec2", "rds", "elb"}


@patch("dashboard.providers.aws.boto3.client")
def test_discover_ec2(mock_client, provider):
    ec2 = MagicMock()
    ec2.describe_instances.return_value = {
        "Reservations": [{
            "Instances": [{
                "InstanceId": "i-123",
                "InstanceType": "t3.micro",
                "State": {"Name": "running"},
                "Tags": [{"Key": "Name", "Value": "web1"}],
                "Platform": "windows",
                "LaunchTime": "2024-01-01T00:00:00Z",
            }]
        }]
    }
    mock_client.return_value = ec2
    resources = provider.discover_resources("cn-north-1", "ec2")
    assert len(resources) == 1
    assert resources[0].id == "i-123"
    assert resources[0].resource_type == "ec2"
    assert resources[0].provider == "aws"
    assert resources[0].unique_id == "aws:ec2:cn-north-1:i-123"
    assert resources[0].name == "web1"
    assert resources[0].status == "running"
    assert resources[0].class_type == "t3.micro"
    assert resources[0].os_or_engine == "Windows"
    assert resources[0].meta["instance_type"] == "t3.micro"
    assert resources[0].meta["region"] == "cn-north-1"
    assert resources[0].meta["os"] == "Windows"


@patch("dashboard.providers.aws.boto3.client")
def test_discover_ec2_linux_no_tags(mock_client, provider):
    ec2 = MagicMock()
    ec2.describe_instances.return_value = {
        "Reservations": [{
            "Instances": [{
                "InstanceId": "i-456",
                "InstanceType": "t3.small",
                "State": {"Name": "stopped"},
                "Tags": [],
            }]
        }]
    }
    mock_client.return_value = ec2
    resources = provider.discover_resources("cn-north-1", "ec2")
    assert len(resources) == 1
    assert resources[0].name == "i-456"
    assert resources[0].os_or_engine == "Linux/Unix"


@patch("dashboard.providers.aws.boto3.client")
def test_discover_rds(mock_client, provider):
    rds = MagicMock()
    rds.describe_db_instances.return_value = {
        "DBInstances": [{
            "DBInstanceIdentifier": "my-db",
            "DBInstanceStatus": "available",
            "Engine": "mysql",
            "DBInstanceClass": "db.t3.micro",
            "DBInstanceArn": "arn:aws:rds:cn-north-1:123456789:db:my-db",
        }]
    }
    rds.list_tags_for_resource.return_value = {"TagList": [{"Key": "Name", "Value": "my-db-name"}]}
    mock_client.return_value = rds
    resources = provider.discover_resources("cn-north-1", "rds")
    assert len(resources) == 1
    assert resources[0].id == "my-db"
    assert resources[0].resource_type == "rds"
    assert resources[0].provider == "aws"
    assert resources[0].unique_id == "aws:rds:cn-north-1:my-db"
    assert resources[0].name == "my-db-name"
    assert resources[0].status == "available"
    assert resources[0].class_type == "db.t3.micro"
    assert resources[0].os_or_engine == "mysql"


@patch("dashboard.providers.aws.boto3.client")
def test_discover_rds_no_arn(mock_client, provider):
    rds = MagicMock()
    rds.describe_db_instances.return_value = {
        "DBInstances": [{
            "DBInstanceIdentifier": "my-db",
            "DBInstanceStatus": "available",
            "Engine": "mysql",
            "DBInstanceClass": "db.t3.micro",
        }]
    }
    mock_client.return_value = rds
    resources = provider.discover_resources("cn-north-1", "rds")
    assert len(resources) == 1
    assert resources[0].name == "my-db"


@patch("dashboard.providers.aws.boto3.client")
def test_get_metrics_ec2(mock_client, provider):
    cw = MagicMock()
    cw.get_metric_statistics.return_value = {
        "Datapoints": [
            {"Timestamp": datetime(2026, 4, 18, 0, 0), "Average": 10.5, "Maximum": 15.0},
            {"Timestamp": datetime(2026, 4, 18, 1, 0), "Average": 20.0, "Maximum": 25.0},
        ]
    }
    mock_client.return_value = cw
    resource = Resource(
        provider="aws",
        resource_type="ec2",
        region="cn-north-1",
        id="i-123",
        name="test",
        status="running",
    )
    metrics = provider.get_metrics(resource, range_days=7)
    assert metrics.resource_id == "aws:ec2:cn-north-1:i-123"
    assert metrics.metric_name == "cpu_utilization"
    assert len(metrics.points_7d) == 2
    assert metrics.current == 15.2  # round((10.5 + 20.0) / 2, 1) = 15.2
    assert metrics.sparkline_7d == [15.2]
    assert metrics.stats_7d is not None
    assert metrics.stats_7d["avg"] == 15.2  # round((10.5 + 20.0) / 2, 1) = round(15.25, 1) = 15.2


@patch("dashboard.providers.aws.boto3.client")
def test_get_metrics_rds(mock_client, provider):
    cw = MagicMock()
    cw.get_metric_statistics.return_value = {"Datapoints": []}
    mock_client.return_value = cw
    resource = Resource(
        provider="aws",
        resource_type="rds",
        region="cn-north-1",
        id="my-db",
        name="test",
        status="available",
    )
    metrics = provider.get_metrics(resource, range_days=7)
    assert metrics.resource_id == "aws:rds:cn-north-1:my-db"
    assert metrics.metric_name == "cpu_utilization"
    assert len(metrics.points_7d) == 0
    assert metrics.current is None
    assert metrics.stats_7d == {"avg": None, "p95": None, "max": None}


@patch("dashboard.providers.aws.boto3.client")
def test_get_metrics_30d(mock_client, provider):
    cw = MagicMock()
    cw.get_metric_statistics.return_value = {
        "Datapoints": [
            {"Timestamp": datetime(2026, 4, 18, 0, 0), "Average": 10.0, "Maximum": 15.0},
        ]
    }
    mock_client.return_value = cw
    resource = Resource(
        provider="aws",
        resource_type="ec2",
        region="cn-north-1",
        id="i-123",
        name="test",
        status="running",
    )
    metrics = provider.get_metrics(resource, range_days=30)
    assert len(metrics.points_30d) == 1
    assert len(metrics.points_7d) == 0
    assert metrics.stats_30d is not None


def test_is_enabled_fallback():
    with patch("dashboard.providers.aws._load_config") as m:
        m.return_value = {}
        provider = AWSProvider()
        assert provider.is_enabled() is True


def test_regions_fallback():
    with patch("dashboard.providers.aws._load_config") as m:
        m.return_value = {"regions": ["us-east-1", "us-west-2"]}
        provider = AWSProvider()
        assert provider.regions() == ["us-east-1", "us-west-2"]


def test_discover_resources_unknown_type(provider):
    resources = provider.discover_resources("cn-north-1", "unknown")
    assert resources == []


def test_get_metrics_unknown_type(provider):
    resource = Resource(
        provider="aws",
        resource_type="unknown",
        region="cn-north-1",
        id="x",
        name="x",
        status="ok",
    )
    metrics = provider.get_metrics(resource, range_days=7)
    assert metrics.points_7d == []
    assert metrics.points_30d == []


@patch("dashboard.providers.aws.boto3.client")
def test_sync_metrics_to_store(mock_client, provider):
    ec2 = MagicMock()
    ec2.describe_instances.return_value = {
        "Reservations": [{
            "Instances": [{
                "InstanceId": "i-123",
                "InstanceType": "t3.micro",
                "State": {"Name": "running"},
                "Tags": [{"Key": "Name", "Value": "web1"}],
            }]
        }]
    }
    cw = MagicMock()
    cw.get_metric_statistics.return_value = {
        "Datapoints": [
            {"Timestamp": datetime(2026, 4, 18, 10, 0), "Average": 10.5, "Maximum": 15.0},
        ]
    }

    def side_effect(service, **kwargs):
        if service == "ec2":
            return ec2
        if service == "cloudwatch":
            return cw
        return MagicMock()

    mock_client.side_effect = side_effect

    store = MagicMock()
    provider.sync_metrics_to_store(store, backfill_days=1)
    assert store.write_hourly.called
    call_args = store.write_hourly.call_args[0][0]
    assert len(call_args) == 1
    resource_id, metric_name, timestamp, value, region = call_args[0]
    assert resource_id == "aws:ec2:cn-north-1:i-123"
    assert metric_name == "CPUUtilization"
    assert isinstance(timestamp, int)
    assert value == 10.5
    assert region == "cn-north-1"
