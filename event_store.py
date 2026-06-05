"""事件记忆层 —— 基于 SQLite + FTS5 的结构化事件存储

设计原则：
- 事件是高度结构化数据，优先用关系查询（时间/类型/实体），FTS5 作为补充全文检索
- 零外部依赖，单文件可备份
- 实体以 JSON 数组存储，查询时用 LIKE '%entity%' 做近似匹配
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("event-store")

DB_NAME = "events.db"

# 允许的事件类型，入库时校验，避免脏数据
EVENT_TYPES = frozenset({"系统变更", "应用发版", "指标异常", "故障处理", "配置变更", "手动记录"})
SEVERITIES = frozenset({"low", "medium", "high", "critical"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _escape_fts5(query: str) -> str:
    """将查询字符串转义为 SQLite FTS5 安全的 MATCH 表达式。

    FTS5 中双引号用于包裹词组，内部的双引号需重复一次。
    我们对每个空格分隔的词单独包裹，保留词的 AND 语义。
    """
    terms = query.strip().split()
    escaped = []
    for term in terms:
        term = term.replace('"', '""')
        escaped.append(f'"{term}"')
    return " ".join(escaped)


def _parse_ts(ts: str | datetime | None) -> str:
    if ts is None:
        return _now_iso()
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%dT%H:%M:%S")
    return ts


def _normalize_entities(entities: list[str] | str | None) -> str:
    if entities is None:
        return "[]"
    if isinstance(entities, str):
        # 尝试解析，如果失败则包装成单元素列表
        try:
            parsed = json.loads(entities)
            if isinstance(parsed, list):
                return json.dumps(parsed, ensure_ascii=False)
        except Exception:
            pass
        return json.dumps([entities], ensure_ascii=False)
    return json.dumps(entities, ensure_ascii=False)


class EventStore:
    def __init__(self, db_path: str | Path = DB_NAME):
        self.db_path = str(db_path)
        self._ensure_schema()
        log.info(f"EventStore 初始化完成，路径: {self.db_path}")

    # ---- 内部工具 ----
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self):
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    entities TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL DEFAULT 'manual',
                    title TEXT NOT NULL,
                    description TEXT,
                    severity TEXT NOT NULL DEFAULT 'medium',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_user_time
                    ON events(user_id, ts);
                CREATE INDEX IF NOT EXISTS idx_events_type
                    ON events(event_type);
                CREATE INDEX IF NOT EXISTS idx_events_source
                    ON events(source);

                -- FTS5 全文索引（contentless 模式节省空间，通过触发器同步）
                CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
                    title, description,
                    content='events',
                    content_rowid='rowid'
                );

                -- 触发器：保持 events_fts 与 events 同步
                CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
                    INSERT INTO events_fts(rowid, title, description)
                    VALUES (new.rowid, new.title, new.description);
                END;

                CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
                    INSERT INTO events_fts(events_fts, rowid, title, description)
                    VALUES ('delete', old.rowid, old.title, old.description);
                END;

                CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE ON events BEGIN
                    INSERT INTO events_fts(events_fts, rowid, title, description)
                    VALUES ('delete', old.rowid, old.title, old.description);
                    INSERT INTO events_fts(rowid, title, description)
                    VALUES (new.rowid, new.title, new.description);
                END;
                """
            )
            conn.commit()

    # ---- 写操作 ----
    def add_events_batch(self, events: list[dict[str, Any]]) -> list[str]:
        """批量插入事件，返回 id 列表。用于初始导入等场景。"""
        eids: list[str] = []
        with self._conn() as conn:
            for payload in events:
                eid = payload.get("event_id") or uuid.uuid4().hex
                eids.append(eid)
                try:
                    conn.execute(
                        """
                        INSERT INTO events
                        (id, user_id, ts, event_type, entities, source, title, description, severity, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            eid,
                            payload["user_id"],
                            _parse_ts(payload.get("ts")),
                            payload["event_type"],
                            _normalize_entities(payload.get("entities")),
                            payload.get("source", "manual"),
                            payload["title"].strip(),
                            payload.get("description", ""),
                            payload.get("severity", "medium"),
                            _now_iso(),
                        ),
                    )
                except sqlite3.IntegrityError:
                    log.warning(f"批量插入跳过重复: {eid}")
            conn.commit()
        return eids

    def add_event(
        self,
        user_id: str,
        title: str,
        description: str = "",
        event_type: str = "手动记录",
        entities: list[str] | str | None = None,
        ts: str | datetime | None = None,
        source: str = "manual",
        severity: str = "medium",
        event_id: str | None = None,
    ) -> str:
        """添加一条事件，返回 event_id"""
        if event_type not in EVENT_TYPES:
            raise ValueError(f"非法 event_type: {event_type}，允许值: {EVENT_TYPES}")
        if severity not in SEVERITIES:
            raise ValueError(f"非法 severity: {severity}，允许值: {SEVERITIES}")
        if not title or not title.strip():
            raise ValueError("title 不能为空")

        eid = event_id or uuid.uuid4().hex
        ts_iso = _parse_ts(ts)
        ent_json = _normalize_entities(entities)
        now = _now_iso()

        with self._conn() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO events
                    (id, user_id, ts, event_type, entities, source, title, description, severity, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (eid, user_id, ts_iso, event_type, ent_json, source, title.strip(), description, severity, now),
                )
                conn.commit()
                log.info(f"事件入库 [{user_id}] {event_type}: {title[:40]}")
                return eid
            except sqlite3.IntegrityError:
                # event_id 冲突（幂等场景）
                log.warning(f"事件已存在，跳过: {eid}")
                return eid

    # ---- 读操作 ----
    def get_event_by_id(self, event_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE id = ?", (event_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["entities"] = json.loads(d["entities"])
            return d

    def search_events(
        self,
        user_id: str,
        query: str | None = None,
        entities: list[str] | None = None,
        event_types: list[str] | None = None,
        days: int = 14,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """综合检索事件

        检索策略（按优先级）：
        1. 时间范围过滤：ts >= now - days
        2. 用户隔离：user_id
        3. 实体过滤：entities JSON 中命中任一实体（LIKE 匹配）
        4. 类型过滤：event_type IN (...)
        5. 全文检索：FTS5 MATCH query（当提供 query 且上述过滤后仍不足 top_k 时，或作为排序加权）
        """
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

        # 基础 SQL 和参数
        where_clauses = ["e.user_id = ?", "e.ts >= ?"]
        params: list[Any] = [user_id, since]

        if event_types:
            placeholders = ",".join("?" * len(event_types))
            where_clauses.append(f"e.event_type IN ({placeholders})")
            params.extend(event_types)

        if entities:
            # entities 字段是 JSON 数组字符串，如 ["test1", "MySQL"]
            # 用 OR 链式 LIKE 子串匹配（宽松匹配，"数据库"可命中"数据库迁移到"）
            ent_likes = " OR ".join(["e.entities LIKE ?"] * len(entities))
            where_clauses.append(f"({ent_likes})")
            params.extend([f"%{ent}%" for ent in entities])

        where_sql = " AND ".join(where_clauses)

        # 如果有 query，用 FTS5 做排序加权；否则按时间倒排
        if query and query.strip():
            sql = f"""
                SELECT e.*,
                       CASE WHEN fts.rowid IS NOT NULL THEN 1 ELSE 0 END AS fts_rank
                FROM events e
                LEFT JOIN events_fts fts ON e.rowid = fts.rowid
                    AND events_fts MATCH ?
                WHERE {where_sql}
                ORDER BY fts_rank DESC, e.ts DESC
                LIMIT ?
            """
            params.insert(0, _escape_fts5(query.strip()))
            params.append(top_k)
        else:
            sql = f"""
                SELECT e.*, 0 AS fts_rank
                FROM events e
                WHERE {where_sql}
                ORDER BY e.ts DESC
                LIMIT ?
            """
            params.append(top_k)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            results = []
            for row in rows:
                d = dict(row)
                d["entities"] = json.loads(d["entities"])
                results.append(d)
            return results

    def list_events(
        self,
        user_id: str,
        days: int = 30,
        event_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """按时间倒序列出事件"""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        where_clauses = ["user_id = ?", "ts >= ?"]
        params: list[Any] = [user_id, since]

        if event_types:
            placeholders = ",".join("?" * len(event_types))
            where_clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_types)

        where_sql = " AND ".join(where_clauses)
        sql = f"SELECT * FROM events WHERE {where_sql} ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            results = []
            for row in rows:
                d = dict(row)
                d["entities"] = json.loads(d["entities"])
                results.append(d)
            return results

    def count(self, user_id: str | None = None) -> int:
        with self._conn() as conn:
            if user_id:
                row = conn.execute("SELECT COUNT(*) FROM events WHERE user_id = ?", (user_id,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM events").fetchone()
            return row[0] if row else 0

    def clear(self, user_id: str | None = None):
        """清空事件。如果指定 user_id，仅清空该用户；否则清空全部"""
        with self._conn() as conn:
            if user_id:
                conn.execute("DELETE FROM events WHERE user_id = ?", (user_id,))
            else:
                conn.execute("DELETE FROM events")
            conn.commit()
        log.info(f"事件已清空 {'user=' + user_id if user_id else '全部'}")
