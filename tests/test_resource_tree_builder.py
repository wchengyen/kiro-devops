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

    # Exact counts: 2 resources + 3 tag groups (Project:myapp, Environment:prod, Environment:dev)
    assert len(graph["nodes"]) == 5
    # Edges: 2 resource->group + 2 group->group = 4
    assert len(graph["edges"]) == 4

    node_ids = {n["id"]: n for n in graph["nodes"]}
    assert "aws:ec2:cn-north-1:i-123" in node_ids
    assert "group:Project:myapp" in node_ids
    assert "group:Environment:prod" in node_ids

    # Verify payload fields
    assert node_ids["aws:ec2:cn-north-1:i-123"]["label"] == "web-01"
    assert node_ids["aws:ec2:cn-north-1:i-123"]["data"]["provider"] == "aws"
    assert node_ids["group:Project:myapp"]["label"] == "Project: myapp"

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
    edge_pairs = {(e["source"], e["target"]): e for e in graph["edges"]}
    assert ("aws:eks:cn-north-1:cluster-1", "aws:ec2:cn-north-1:i-123") in edge_pairs
    edge = edge_pairs[("aws:eks:cn-north-1:cluster-1", "aws:ec2:cn-north-1:i-123")]
    assert edge["relation_type"] == "contains"
    assert edge["source_origin"] == "auto_scan"


def test_build_graph_filters_dangling_edges(builder):
    resources = [
        Resource(
            provider="aws", resource_type="ec2", region="cn-north-1",
            id="i-123", name="web-01", status="running", tags={}
        ),
    ]
    relations = [
        {
            "source_id": "aws:ec2:cn-north-1:i-123",
            "target_id": "aws:ec2:cn-north-1:i-does-not-exist",
            "relation_type": "contains",
            "source_origin": "auto_scan",
        }
    ]
    graph = builder.build_graph(resources, relations, [], {})
    assert len(graph["edges"]) == 0


def test_build_graph_empty(builder):
    graph = builder.build_graph([], [], [], {})
    assert graph["nodes"] == []
    assert graph["edges"] == []


def test_build_graph_applies_positions(builder):
    resources = [
        Resource(
            provider="aws", resource_type="ec2", region="cn-north-1",
            id="i-123", name="web-01", status="running",
            tags={"Project": "myapp"}
        ),
    ]
    positions = {
        "aws:ec2:cn-north-1:i-123": {"x": 10, "y": 20},
        "group:Project:myapp": {"x": 30, "y": 40},
    }
    graph = builder.build_graph(resources, [], ["Project"], positions)
    node_map = {n["id"]: n for n in graph["nodes"]}
    assert node_map["aws:ec2:cn-north-1:i-123"]["position"] == {"x": 10, "y": 20}
    assert node_map["group:Project:myapp"]["position"] == {"x": 30, "y": 40}
