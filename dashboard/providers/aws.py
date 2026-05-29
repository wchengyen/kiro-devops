import datetime
import json
import logging
import os
from collections import defaultdict
from typing import List, Optional

try:
    import boto3
except ImportError:
    boto3 = None

from dashboard.providers.base import BaseResourceProvider, Resource, ResourceMetrics, MetricPoint

logger = logging.getLogger(__name__)


def _load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "dashboard_config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)
    return {}


class AWSProvider(BaseResourceProvider):
    @property
    def name(self) -> str:
        return "aws"

    def is_enabled(self) -> bool:
        config = _load_config()
        aws_config = config.get("providers", {}).get("aws", {})
        return aws_config.get("enabled", True)

    def regions(self) -> List[str]:
        config = _load_config()
        aws_config = config.get("providers", {}).get("aws", {})
        regions = aws_config.get("regions")
        if regions is not None:
            return regions
        # Fallback to top-level regions for backward compatibility
        return config.get("regions", [])

    def resource_types(self) -> List[str]:
        return ["ec2", "rds", "elb"]

    def discover_resources(self, region: str, resource_type: Optional[str] = None) -> List[Resource]:
        if resource_type == "ec2":
            return self._discover_ec2(region)
        elif resource_type == "rds":
            return self._discover_rds(region)
        elif resource_type == "elb":
            return self._discover_elb(region)
        return []

    def _discover_ec2(self, region: str) -> List[Resource]:
        if boto3 is None:
            return []
        client = boto3.client("ec2", region_name=region)
        resp = client.describe_instances(
            Filters=[{"Name": "instance-state-name", "Values": ["running", "stopped"]}]
        )
        resources = []
        for reservation in resp.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                tags = {tag.get("Key", ""): tag.get("Value", "") for tag in inst.get("Tags", [])}
                name = tags.get("Name", "")
                platform = inst.get("Platform")
                os_name = "Windows" if platform == "windows" else "Linux/Unix"
                resources.append(
                    Resource(
                        provider="aws",
                        resource_type="ec2",
                        region=region,
                        id=inst["InstanceId"],
                        name=name or inst["InstanceId"],
                        status=inst["State"]["Name"],
                        class_type=inst.get("InstanceType", ""),
                        os_or_engine=os_name,
                        tags=tags,
                        meta={
                            "instance_type": inst.get("InstanceType", ""),
                            "region": region,
                            "os": os_name,
                        },
                    )
                )
        return resources

    def _discover_elb(self, region: str) -> List[Resource]:
        if boto3 is None:
            return []
        client = boto3.client("elbv2", region_name=region)
        resp = client.describe_load_balancers()
        resources = []
        for lb in resp.get("LoadBalancers", []):
            lb_arn = lb.get("LoadBalancerArn", "")
            lb_name = lb.get("LoadBalancerName", "")
            tags = {}
            if lb_arn:
                try:
                    tag_resp = client.describe_tags(ResourceArns=[lb_arn])
                    for tag_desc in tag_resp.get("TagDescriptions", []):
                        tags = {tag.get("Key", ""): tag.get("Value", "") for tag in tag_desc.get("Tags", [])}
                        break
                except Exception as e:
                    logger.warning("Failed to list tags for ELB %s: %s", lb_arn, e)
            name = tags.get("Name", lb_name)
            resources.append(
                Resource(
                    provider="aws",
                    resource_type="elb",
                    region=region,
                    id=lb_name,
                    name=name or lb_name,
                    status=(lb.get("State", {}) or {}).get("Code", "").lower() if isinstance(lb.get("State"), dict) else str(lb.get("State", "")).lower(),
                    class_type=lb.get("Type", ""),
                    os_or_engine="",
                    tags=tags,
                    meta={
                        "type": lb.get("Type", ""),
                        "scheme": lb.get("Scheme", ""),
                        "dns_name": lb.get("DNSName", ""),
                        "region": region,
                    },
                )
            )
        return resources

    def _discover_rds(self, region: str) -> List[Resource]:
        if boto3 is None:
            return []
        client = boto3.client("rds", region_name=region)
        resp = client.describe_db_instances()
        resources = []
        for db in resp.get("DBInstances", []):
            tags = {}
            db_arn = db.get("DBInstanceArn", "")
            if db_arn:
                try:
                    tag_resp = client.list_tags_for_resource(ResourceName=db_arn)
                    tags = {tag.get("Key", ""): tag.get("Value", "") for tag in tag_resp.get("TagList", [])}
                except Exception as e:
                    logger.warning("Failed to list tags for RDS %s: %s", db_arn, e)
            name = tags.get("Name", db["DBInstanceIdentifier"])
            resources.append(
                Resource(
                    provider="aws",
                    resource_type="rds",
                    region=region,
                    id=db["DBInstanceIdentifier"],
                    name=name,
                    status=db["DBInstanceStatus"],
                    class_type=db.get("DBInstanceClass", ""),
                    os_or_engine=db.get("Engine", ""),
                    tags=tags,
                    meta={
                        "engine": db.get("Engine", ""),
                        "region": region,
                        "db_instance_class": db.get("DBInstanceClass", ""),
                    },
                )
            )
        return resources

    def _fetch_cloudwatch_datapoints(
        self, resource_id: str, namespace: str, dimension_name: str, days: int = 7, region: str | None = None
    ) -> list[dict]:
        """Fetch raw CloudWatch Datapoints (with Average and Maximum)."""
        if boto3 is None:
            return []
        kwargs = {"region_name": region} if region else {}
        client = boto3.client("cloudwatch", **kwargs)
        end = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        start = end - datetime.timedelta(days=days)
        resp = client.get_metric_statistics(
            Namespace=namespace,
            MetricName="CPUUtilization",
            Dimensions=[{"Name": dimension_name, "Value": resource_id}],
            StartTime=start,
            EndTime=end,
            Period=3600,
            Statistics=["Average", "Maximum"],
        )
        return sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])

    def get_metrics(self, resource: Resource, range_days: int = 7) -> ResourceMetrics:
        if boto3 is None:
            return ResourceMetrics(
                resource_id=resource.unique_id,
                metric_name="cpu_utilization",
                points_7d=[],
                points_30d=[],
            )

        if resource.resource_type == "ec2":
            namespace = "AWS/EC2"
            dimension_name = "InstanceId"
        elif resource.resource_type == "rds":
            namespace = "AWS/RDS"
            dimension_name = "DBInstanceIdentifier"
        else:
            return ResourceMetrics(
                resource_id=resource.unique_id,
                metric_name="cpu_utilization",
                points_7d=[],
                points_30d=[],
            )

        points = self._fetch_cloudwatch_datapoints(
            resource.id, namespace, dimension_name, range_days, resource.region
        )
        metric_points = [
            MetricPoint(timestamp=p["Timestamp"], value=p["Average"])
            for p in points
        ]

        # Compute sparkline and stats
        daily = defaultdict(list)
        for p in metric_points:
            day = p.timestamp.strftime("%Y-%m-%d")
            daily[day].append(p.value)
        sparkline = [round(sum(v) / len(v), 1) for v in daily.values()]
        current = sparkline[-1] if sparkline else None

        averages = [p["Average"] for p in points]
        maxima = [p["Maximum"] for p in points]
        if averages:
            sorted_avgs = sorted(averages)
            idx = int(len(sorted_avgs) * 0.95)
            p95_val = sorted_avgs[min(idx, len(sorted_avgs) - 1)]
            stats = {
                "avg": round(sum(averages) / len(averages), 1),
                "p95": round(p95_val, 1),
                "max": round(max(maxima), 1),
            }
        else:
            stats = {"avg": None, "p95": None, "max": None}

        if range_days <= 7:
            points_7d = metric_points
            points_30d = []
            stats_7d = stats
            stats_30d = None
        else:
            points_7d = []
            points_30d = metric_points
            stats_7d = None
            stats_30d = stats

        return ResourceMetrics(
            resource_id=resource.unique_id,
            metric_name="cpu_utilization",
            points_7d=points_7d,
            points_30d=points_30d,
            current=current,
            stats_7d=stats_7d,
            stats_30d=stats_30d,
            sparkline_7d=sparkline,
        )

    def sync_metrics_to_store(self, store, backfill_days: int = 1) -> None:
        for region in self.regions():
            for rt in self.resource_types():
                for resource in self.discover_resources(region, rt):
                    metrics = self.get_metrics(resource, range_days=backfill_days)
                    points = metrics.points_7d or metrics.points_30d
                    records = []
                    for p in points:
                        ts = int(p.timestamp.replace(tzinfo=datetime.timezone.utc).timestamp())
                        ts = ts // 3600 * 3600
                        records.append((resource.unique_id, "CPUUtilization", ts, round(p.value, 2), resource.region))
                    if records:
                        store.write_hourly(records)
