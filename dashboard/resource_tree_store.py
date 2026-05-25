import os
import sqlite3
import uuid
from datetime import datetime, timezone


class ResourceTreeStore:
    """SQLite-backed store for resource relations and node positions."""

    def __init__(self, db_path: str = "memory_db/resource_tree.db"):
        """Initialize the store and create tables if they do not exist."""
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Create tables and indexes."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS resource_relations (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    source_origin TEXT NOT NULL,
                    provider TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_relations_source ON resource_relations(source_id);
                CREATE INDEX IF NOT EXISTS idx_relations_target ON resource_relations(target_id);
                CREATE INDEX IF NOT EXISTS idx_relations_origin ON resource_relations(source_origin);
                CREATE INDEX IF NOT EXISTS idx_relations_provider ON resource_relations(provider);

                CREATE TABLE IF NOT EXISTS node_positions (
                    id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    layout_name TEXT NOT NULL DEFAULT 'default',
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(node_id, layout_name)
                );
                CREATE INDEX IF NOT EXISTS idx_node_positions_layout ON node_positions(layout_name);
                """
            )

    def add_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        source_origin: str,
        provider: str | None = None,
    ) -> str:
        """Add a resource relation and return its generated id."""
        rid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO resource_relations (id, source_id, target_id, relation_type, source_origin, provider, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rid, source_id, target_id, relation_type, source_origin, provider, now, now),
            )
        return rid

    def get_relations(self, provider: str | None = None) -> list[dict]:
        """Return all relations, optionally filtered by provider."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if provider:
                rows = conn.execute(
                    "SELECT * FROM resource_relations WHERE provider = ? ORDER BY created_at",
                    (provider,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM resource_relations ORDER BY created_at").fetchall()
            return [dict(r) for r in rows]

    def delete_relation(self, relation_id: str) -> bool:
        """Delete a relation by id. Returns True if a row was deleted."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM resource_relations WHERE id = ?", (relation_id,))
            return cur.rowcount > 0

    def clear_auto_scan_relations(self, provider: str) -> int:
        """Delete auto-scanned relations for a provider. Returns number of rows deleted."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM resource_relations WHERE provider = ? AND source_origin = 'auto_scan'",
                (provider,),
            )
            return cur.rowcount

    def save_positions(self, positions: dict[str, dict], layout_name: str = "default") -> None:
        """Save or update node positions for a layout."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            for node_id, pos in positions.items():
                if "x" not in pos or "y" not in pos:
                    raise ValueError(f"Position for node '{node_id}' must contain 'x' and 'y' keys")
                conn.execute(
                    """
                    INSERT INTO node_positions (id, node_id, layout_name, x, y, updated_at)
                    VALUES (
                        COALESCE((SELECT id FROM node_positions WHERE node_id = ? AND layout_name = ?), ?),
                        ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(node_id, layout_name) DO UPDATE SET
                        x = excluded.x, y = excluded.y, updated_at = excluded.updated_at
                    """,
                    (node_id, layout_name, str(uuid.uuid4()), node_id, layout_name, pos["x"], pos["y"], now),
                )

    def get_positions(self, layout_name: str = "default") -> dict[str, dict]:
        """Return node positions for a layout as a dict mapping node_id to {x, y}."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT node_id, x, y FROM node_positions WHERE layout_name = ?",
                (layout_name,),
            ).fetchall()
            return {r["node_id"]: {"x": r["x"], "y": r["y"]} for r in rows}
