import os
import pytest
from dashboard.resource_tree_store import ResourceTreeStore

@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "resource_tree.db")
    return ResourceTreeStore(db_path)


def test_add_and_get_relation(store):
    rid = store.add_relation(
        source_id="aws:eks:cn-north-1:cluster-1",
        target_id="aws:ec2:cn-north-1:i-123",
        relation_type="contains",
        source_origin="auto_scan",
        provider="aws",
    )
    assert rid is not None
    relations = store.get_relations()
    assert len(relations) == 1
    assert relations[0]["source_id"] == "aws:eks:cn-north-1:cluster-1"


def test_clear_auto_scan_relations(store):
    store.add_relation("a", "b", "contains", "auto_scan", "aws")
    store.add_relation("a", "c", "contains", "manual", "aws")
    deleted = store.clear_auto_scan_relations("aws")
    assert deleted == 1
    remaining = store.get_relations()
    assert len(remaining) == 1
    assert remaining[0]["source_origin"] == "manual"


def test_positions_crud(store):
    store.save_positions({"node-a": {"x": 10, "y": 20}, "node-b": {"x": 30, "y": 40}})
    positions = store.get_positions()
    assert positions["node-a"]["x"] == 10
    assert positions["node-b"]["y"] == 40
