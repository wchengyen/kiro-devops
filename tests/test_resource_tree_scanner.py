import pytest
from unittest.mock import MagicMock, patch
from dashboard.resource_tree import AWSResourceScanner


def _mock_boto_client(service, region_name=None):
    client = MagicMock()
    if service == "eks":
        client.list_clusters.return_value = {"clusters": ["cluster-1"]}
        client.list_nodegroups.return_value = {"nodegroups": ["ng-1"]}
        client.describe_nodegroup.return_value = {
            "nodegroup": {
                "resources": {
                    "autoScalingGroups": [{"name": "asg-1"}]
                }
            }
        }
    elif service == "autoscaling":
        client.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [
                {"Instances": [{"InstanceId": "i-123"}]}
            ]
        }
    elif service == "elbv2":
        client.describe_load_balancers.return_value = {
            "LoadBalancers": [
                {"LoadBalancerArn": "arn:aws:elasticloadbalancing:cn-north-1:123:loadbalancer/app/lb-1/abc", "LoadBalancerName": "lb-1"}
            ]
        }
        client.describe_target_groups.return_value = {
            "TargetGroups": [{"TargetGroupArn": "arn:aws:elasticloadbalancing:cn-north-1:123:targetgroup/tg-1/abc"}]
        }
        client.describe_target_health.return_value = {
            "TargetHealthDescriptions": [
                {"Target": {"Id": "i-123"}}
            ]
        }
    elif service == "ec2":
        client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-123",
                            "SubnetId": "subnet-1",
                            "VpcId": "vpc-1",
                            "SecurityGroups": [{"GroupId": "sg-1"}],
                        }
                    ]
                }
            ]
        }
    elif service == "rds":
        client.describe_db_instances.return_value = {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": "db-1",
                    "DBSubnetGroup": {
                        "DBSubnetGroupName": "db-subnet-group-1",
                        "VpcId": "vpc-1",
                        "Subnets": [{"SubnetIdentifier": "subnet-2"}],
                    }
                }
            ]
        }
    return client


@patch("dashboard.resource_tree.boto3")
def test_scan_eks_ec2(mock_boto3):
    mock_boto3.client.side_effect = _mock_boto_client
    scanner = AWSResourceScanner()
    relations = scanner._scan_eks_ec2("cn-north-1")

    assert len(relations) == 1
    assert relations[0]["source_id"] == "aws:eks:cn-north-1:cluster-1"
    assert relations[0]["target_id"] == "aws:ec2:cn-north-1:i-123"
    assert relations[0]["relation_type"] == "contains"


@patch("dashboard.resource_tree.boto3")
def test_scan_elb_ec2(mock_boto3):
    mock_boto3.client.side_effect = _mock_boto_client
    scanner = AWSResourceScanner()
    relations = scanner._scan_elb_targets("cn-north-1")

    assert len(relations) == 1
    assert relations[0]["source_id"] == "aws:elb:cn-north-1:lb-1"
    assert relations[0]["target_id"] == "aws:ec2:cn-north-1:i-123"
    assert relations[0]["relation_type"] == "attached_to"


@patch("dashboard.resource_tree.boto3")
def test_scan_ec2_network(mock_boto3):
    mock_boto3.client.side_effect = _mock_boto_client
    scanner = AWSResourceScanner()
    relations = scanner._scan_ec2_network("cn-north-1")

    subnet_edges = [r for r in relations if "subnet" in r["target_id"]]
    vpc_edges = [r for r in relations if "vpc" in r["target_id"]]

    assert len(subnet_edges) == 1
    assert subnet_edges[0]["source_id"] == "aws:ec2:cn-north-1:i-123"
    assert subnet_edges[0]["target_id"] == "aws:subnet:cn-north-1:subnet-1"
    assert subnet_edges[0]["relation_type"] == "belongs_to"

    assert len(vpc_edges) == 1
    assert vpc_edges[0]["source_id"] == "aws:subnet:cn-north-1:subnet-1"
    assert vpc_edges[0]["target_id"] == "aws:vpc:cn-north-1:vpc-1"
    assert vpc_edges[0]["relation_type"] == "belongs_to"


@patch("dashboard.resource_tree.boto3")
def test_scan_rds_network(mock_boto3):
    mock_boto3.client.side_effect = _mock_boto_client
    scanner = AWSResourceScanner()
    relations = scanner._scan_rds_network("cn-north-1")

    vpc_edges = [r for r in relations if "vpc" in r["target_id"]]
    subnet_edges = [r for r in relations if "subnet" in r["target_id"]]

    assert len(vpc_edges) == 1
    assert vpc_edges[0]["source_id"] == "aws:rds:cn-north-1:db-1"
    assert vpc_edges[0]["target_id"] == "aws:vpc:cn-north-1:vpc-1"
    assert vpc_edges[0]["relation_type"] == "belongs_to"

    assert len(subnet_edges) == 1
    assert subnet_edges[0]["source_id"] == "aws:rds:cn-north-1:db-1"
    assert subnet_edges[0]["target_id"] == "aws:subnet:cn-north-1:subnet-2"
    assert subnet_edges[0]["relation_type"] == "belongs_to"
