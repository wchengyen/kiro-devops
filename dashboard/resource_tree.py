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
                "position": pos if pos else None,
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
                        "position": positions.get(group_id, None),
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
            if sid in node_ids or tid in node_ids:
                edges.append({
                    "id": rel.get("id", f"{sid}->{tid}"),
                    "source": sid,
                    "target": tid,
                    "relation_type": rel["relation_type"],
                    "source_origin": rel["source_origin"],
                })

        return {"nodes": nodes, "edges": edges}
