import pytest
from dashboard.resource_tree import ResourceTreeBuilder
from dashboard.providers.base import Resource


@pytest.fixture
def builder():
    return ResourceTreeBuilder()


def test_build_graph_with_tag_groups(builder):
    resources = [
        Resource(
            provider="aws", resource_type="ec2", region="cn-north-1",
            id="i-123", name="web-01", status="running",
            tags={"Project": "myapp", "Environment": "prod"}
        ),
        Resource(
            provider="aws", resource_type="ec2", region="cn-north-1",
            id="i-456", name="web-02", status="running",
            tags={"Project": "myapp", "Environment": "dev"}
        ),
    ]
    relations = []
    group_by_tags = ["Project", "Environment"]
    positions = {}

    graph = builder.build_graph(resources, relations, group_by_tags, positions)

    node_ids = {n["id"] for n in graph["nodes"]}
    assert "aws:ec2:cn-north-1:i-123" in node_ids
    assert "group:Project:myapp" in node_ids
    assert "group:Environment:prod" in node_ids

    edge_pairs = {(e["source"], e["target"]) for e in graph["edges"]}
    assert ("aws:ec2:cn-north-1:i-123", "group:Environment:prod") in edge_pairs
    assert ("group:Environment:prod", "group:Project:myapp") in edge_pairs


def test_build_graph_with_auto_scan_relations(builder):
    resources = [
        Resource(
            provider="aws", resource_type="eks", region="cn-north-1",
            id="cluster-1", name="prod-cluster", status="ACTIVE", tags={}
        ),
        Resource(
            provider="aws", resource_type="ec2", region="cn-north-1",
            id="i-123", name="node-1", status="running", tags={}
        ),
    ]
    relations = [
        {
            "source_id": "aws:eks:cn-north-1:cluster-1",
            "target_id": "aws:ec2:cn-north-1:i-123",
            "relation_type": "contains",
            "source_origin": "auto_scan",
        }
    ]
    graph = builder.build_graph(resources, relations, [], {})
    edge_pairs = {(e["source"], e["target"]) for e in graph["edges"]}
    assert ("aws:eks:cn-north-1:cluster-1", "aws:ec2:cn-north-1:i-123") in edge_pairs
