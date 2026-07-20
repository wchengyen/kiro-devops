from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .models import ExecutionContext


@dataclass(frozen=True)
class SessionRecord:
    principal_key: str
    kiro_session_id: str
    profile_id: str
    profile_fingerprint: str
    short_id: int
    topic: str
    created_at: float
    last_active: float
    message_count: int


class SessionStore:
    def __init__(self, db_path: str | Path, *, max_sessions_per_principal: int = 20):
        self.db_path = str(db_path)
        self.max_sessions_per_principal = max_sessions_per_principal
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tenant_sessions (
                    principal_key TEXT NOT NULL,
                    kiro_session_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    profile_fingerprint TEXT NOT NULL,
                    short_id INTEGER NOT NULL,
                    topic TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_active REAL NOT NULL,
                    message_count INTEGER NOT NULL,
                    PRIMARY KEY (principal_key, kiro_session_id),
                    UNIQUE (principal_key, short_id)
                );
                CREATE INDEX IF NOT EXISTS idx_tenant_sessions_active
                    ON tenant_sessions(principal_key, last_active DESC);
                """
            )

    @staticmethod
    def _record(row: sqlite3.Row | None) -> SessionRecord | None:
        return SessionRecord(**dict(row)) if row is not None else None

    def register_new(
        self,
        context: ExecutionContext,
        session_id: str,
        topic: str,
        *,
        now: float | None = None,
    ) -> SessionRecord:
        timestamp = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(short_id), 0) + 1 AS next_id "
                "FROM tenant_sessions WHERE principal_key = ?",
                (context.principal_key,),
            ).fetchone()
            short_id = int(row["next_id"])
            conn.execute(
                """
                INSERT INTO tenant_sessions (
                    principal_key, kiro_session_id, profile_id,
                    profile_fingerprint, short_id, topic,
                    created_at, last_active, message_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    context.principal_key,
                    session_id,
                    context.profile_id,
                    context.profile_fingerprint,
                    short_id,
                    topic[:30],
                    timestamp,
                    timestamp,
                ),
            )
            conn.execute(
                """
                DELETE FROM tenant_sessions
                WHERE principal_key = ? AND kiro_session_id NOT IN (
                    SELECT kiro_session_id FROM tenant_sessions
                    WHERE principal_key = ?
                    ORDER BY short_id DESC LIMIT ?
                )
                """,
                (
                    context.principal_key,
                    context.principal_key,
                    self.max_sessions_per_principal,
                ),
            )
            conn.commit()
        return self.get_by_short_id(context, short_id)

    def resolve_active(
        self,
        context: ExecutionContext,
        *,
        now: float | None = None,
        timeout: int = 1800,
    ) -> SessionRecord | None:
        timestamp = time.time() if now is None else now
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM tenant_sessions
                WHERE principal_key = ?
                ORDER BY last_active DESC, short_id DESC LIMIT 1
                """,
                (context.principal_key,),
            ).fetchone()
        record = self._record(row)
        if record is None:
            return None
        if record.last_active <= 0 or timestamp - record.last_active >= timeout:
            return None
        if record.profile_fingerprint != context.profile_fingerprint:
            return None
        return record

    def touch(
        self,
        context: ExecutionContext,
        session_id: str,
        *,
        now: float | None = None,
    ) -> None:
        timestamp = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tenant_sessions
                SET last_active = ?, message_count = message_count + 1
                WHERE principal_key = ? AND kiro_session_id = ?
                  AND profile_fingerprint = ?
                """,
                (
                    timestamp,
                    context.principal_key,
                    session_id,
                    context.profile_fingerprint,
                ),
            )
            conn.commit()

    def clear_active(self, principal_key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tenant_sessions SET last_active = 0 WHERE principal_key = ?",
                (principal_key,),
            )
            conn.commit()

    def get_by_short_id(
        self,
        context: ExecutionContext,
        short_id: int,
    ) -> SessionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM tenant_sessions
                WHERE principal_key = ? AND short_id = ?
                  AND profile_fingerprint = ?
                """,
                (context.principal_key, short_id, context.profile_fingerprint),
            ).fetchone()
        return self._record(row)

    def list_sessions(
        self,
        context: ExecutionContext,
        *,
        limit: int = 10,
    ) -> list[SessionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tenant_sessions
                WHERE principal_key = ? AND profile_fingerprint = ?
                ORDER BY last_active DESC, short_id DESC LIMIT ?
                """,
                (context.principal_key, context.profile_fingerprint, limit),
            ).fetchall()
        return [self._record(row) for row in rows]
