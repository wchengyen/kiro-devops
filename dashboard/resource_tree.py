import json
import os

import boto3
from typing import Any
from dashboard.providers.base import Resource


class ResourceTreeBuilder:
    def build_graph(
        self,
        resources: list[Resource],
        relations: list[dict],
        group_by_tags: list[str],
        positions: dict[str, dict],
    ) -> dict:
        nodes: list[dict] = []
        edges: list[dict] = []
        node_ids: set[str] = set()

        # Build resource nodes
        for r in resources:
            nid = r.unique_id
            node_ids.add(nid)
            pos = positions.get(nid, {})
            nodes.append({
                "id": nid,
                "label": r.name,
                "type": r.resource_type,
                "position": pos or None,
                "data": {
                    "provider": r.provider,
                    "region": r.region,
                    "status": r.status,
                    "tags": r.tags,
                    "class_type": r.class_type,
                    "os_or_engine": r.os_or_engine,
                },
            })

        # Build tag group nodes and edges
        group_nodes: dict[str, dict] = {}
        for r in resources:
            prev_group_id: str | None = None
            for tag_key in group_by_tags:
                tag_value = r.tags.get(tag_key)
                if not tag_value:
                    continue
                group_id = f"group:{tag_key}:{tag_value}"
                if group_id not in group_nodes:
                    group_nodes[group_id] = {
                        "id": group_id,
                        "label": f"{tag_key}: {tag_value}",
                        "type": "tag_group",
                        "is_group": True,
                        "position": positions.get(group_id),
                        "data": {},
                    }
                    node_ids.add(group_id)
                if prev_group_id:
                    edges.append({
                        "id": f"{group_id}->{prev_group_id}",
                        "source": group_id,
                        "target": prev_group_id,
                        "relation_type": "grouped_by",
                        "source_origin": "tag_group",
                    })
                prev_group_id = group_id
            # Link last group to resource
            if prev_group_id:
                edges.append({
                    "id": f"{r.unique_id}->{prev_group_id}",
                    "source": r.unique_id,
                    "target": prev_group_id,
                    "relation_type": "grouped_by",
                    "source_origin": "tag_group",
                })

        nodes.extend(group_nodes.values())

        # Build auto_scan / manual relation edges
        for rel in relations:
            sid = rel["source_id"]
            tid = rel["target_id"]
            # Only add edges where both nodes exist in our graph
            if sid in node_ids and tid in node_ids:
                edges.append({
                    "id": rel.get("id", f"{sid}->{tid}"),
                    "source": sid,
                    "target": tid,
                    "relation_type": rel["relation_type"],
                    "source_origin": rel["source_origin"],
                })

        return {"nodes": nodes, "edges": edges}


def _load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "..", "dashboard_config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)
    return {}


class AWSResourceScanner:
    def __init__(self):
        pass

    def scan(self, regions: list[str]) -> list[dict]:
        relations: list[dict] = []
        for region in regions:
            relations.extend(self._scan_eks_ec2(region))
            relations.extend(self._scan_elb_targets(region))
            relations.extend(self._scan_ec2_network(region))
            relations.extend(self._scan_rds_network(region))
        return relations

    def _scan_eks_ec2(self, region: str) -> list[dict]:
        relations: list[dict] = []
        try:
            eks = boto3.client("eks", region_name=region)
            asg = boto3.client("autoscaling", region_name=region)
            clusters = eks.list_clusters()["clusters"]
            for cluster_name in clusters:
                nodegroups = eks.list_nodegroups(clusterName=cluster_name)["nodegroups"]
                for ng_name in nodegroups:
                    ng = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=ng_name)["nodegroup"]
                    for asg_info in ng.get("resources", {}).get("autoScalingGroups", []):
                        asg_detail = asg.describe_auto_scaling_groups(
                            AutoScalingGroupNames=[asg_info["name"]]
                        )["AutoScalingGroups"][0]
                        for instance in asg_detail.get("Instances", []):
                            relations.append({
                                "source_id": f"aws:eks:{region}:{cluster_name}",
                                "target_id": f"aws:ec2:{region}:{instance['InstanceId']}",
                                "relation_type": "contains",
                            })
        except Exception:
            pass
        return relations

    def _scan_elb_targets(self, region: str) -> list[dict]:
        relations: list[dict] = []
        try:
            elbv2 = boto3.client("elbv2", region_name=region)
            lbs = elbv2.describe_load_balancers()["LoadBalancers"]
            for lb in lbs:
                lb_arn = lb["LoadBalancerArn"]
                lb_id = lb_arn.split("/")[-1]
                tgs = elbv2.describe_target_groups(LoadBalancerArn=lb_arn)["TargetGroups"]
                for tg in tgs:
                    health = elbv2.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])["TargetHealthDescriptions"]
                    for target in health:
                        target_id = target["Target"]["Id"]
                        if target_id.startswith("i-"):
                            relations.append({
                                "source_id": f"aws:elb:{region}:{lb_id}",
                                "target_id": f"aws:ec2:{region}:{target_id}",
                                "relation_type": "attached_to",
                            })
        except Exception:
            pass
        return relations

    def _scan_ec2_network(self, region: str) -> list[dict]:
        relations: list[dict] = []
        try:
            ec2 = boto3.client("ec2", region_name=region)
            instances = ec2.describe_instances()["Reservations"]
            for reservation in instances:
                for inst in reservation["Instances"]:
                    instance_id = inst["InstanceId"]
                    subnet_id = inst.get("SubnetId")
                    vpc_id = inst.get("VpcId")
                    if subnet_id:
                        relations.append({
                            "source_id": f"aws:ec2:{region}:{instance_id}",
                            "target_id": f"aws:subnet:{region}:{subnet_id}",
                            "relation_type": "belongs_to",
                        })
                    if vpc_id:
                        relations.append({
                            "source_id": f"aws:subnet:{region}:{subnet_id}",
                            "target_id": f"aws:vpc:{region}:{vpc_id}",
                            "relation_type": "belongs_to",
                        })
        except Exception:
            pass
        return relations

    def _scan_rds_network(self, region: str) -> list[dict]:
        relations: list[dict] = []
        try:
            rds = boto3.client("rds", region_name=region)
            dbs = rds.describe_db_instances()["DBInstances"]
            for db in dbs:
                db_id = db["DBInstanceIdentifier"]
                subnet_group = db.get("DBSubnetGroup", {})
                vpc_id = subnet_group.get("VpcId")
                if vpc_id:
                    relations.append({
                        "source_id": f"aws:rds:{region}:{db_id}",
                        "target_id": f"aws:vpc:{region}:{vpc_id}",
                        "relation_type": "belongs_to",
                    })
                for subnet in subnet_group.get("Subnets", []):
                    subnet_id = subnet["SubnetIdentifier"]
                    relations.append({
                        "source_id": f"aws:rds:{region}:{db_id}",
                        "target_id": f"aws:subnet:{region}:{subnet_id}",
                        "relation_type": "belongs_to",
                    })
        except Exception:
            pass
        return relations
