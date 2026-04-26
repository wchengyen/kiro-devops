import os
import sqlite3
from datetime import datetime, timedelta


DEFAULT_BASE_DIR = os.path.join(os.path.dirname(__file__), "..", "memory_db")


def _raw_db_path(year: int, month: int, base_dir: str | None = None) -> str:
    d = base_dir or DEFAULT_BASE_DIR
    return os.path.join(d, f"raw_metrics_{year}_{month:02d}.db")


def _aggregated_db_path(base_dir: str | None = None) -> str:
    d = base_dir or DEFAULT_BASE_DIR
    return os.path.join(d, "aggregated_metrics.db")


def _ensure_hourly_table(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hourly_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource_id TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            value REAL NOT NULL,
            region TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now')),
            UNIQUE(resource_id, metric_name, timestamp)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hourly_lookup ON hourly_metrics(resource_id, metric_name, timestamp)"
    )
    conn.commit()


def _ensure_daily_table(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_aggregated (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource_id TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            date TEXT NOT NULL,
            min_value REAL NOT NULL,
            avg_value REAL NOT NULL,
            p95_value REAL NOT NULL,
            max_value REAL NOT NULL,
            region TEXT,
            UNIQUE(resource_id, metric_name, date)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_daily_lookup ON daily_aggregated(resource_id, metric_name, date)"
    )
    conn.commit()


class MetricsStore:
    def __init__(self, base_dir: str | None = None):
        self.base_dir = base_dir or DEFAULT_BASE_DIR
        os.makedirs(self.base_dir, exist_ok=True)
        self._raw_conns: dict[str, sqlite3.Connection] = {}
        self._agg_connection: sqlite3.Connection | None = None

    def _raw_conn(self, year: int, month: int) -> sqlite3.Connection:
        key = f"{year}_{month:02d}"
        if key not in self._raw_conns:
            path = _raw_db_path(year, month, self.base_dir)
            conn = sqlite3.connect(path)
            _ensure_hourly_table(conn)
            self._raw_conns[key] = conn
        return self._raw_conns[key]

    def _agg_conn(self) -> sqlite3.Connection:
        if self._agg_connection is None:
            path = _aggregated_db_path(self.base_dir)
            conn = sqlite3.connect(path)
            _ensure_daily_table(conn)
            self._agg_connection = conn
        return self._agg_connection

    def write_hourly(self, records: list[tuple]):
        """Bulk insert hourly records with UPSERT.

        records: list of (resource_id, metric_name, timestamp, value, region)
        """
        if not records:
            return
        # Group by (year, month) to write to correct DB
        grouped: dict[tuple[int, int], list[tuple]] = {}
        for r in records:
            ts = r[2]
            dt = datetime.utcfromtimestamp(ts)
            key = (dt.year, dt.month)
            grouped.setdefault(key, []).append(r)

        for (year, month), rows in grouped.items():
            conn = self._raw_conn(year, month)
            conn.executemany(
                """
                INSERT INTO hourly_metrics (resource_id, metric_name, timestamp, value, region)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(resource_id, metric_name, timestamp) DO UPDATE SET
                    value=excluded.value,
                    region=excluded.region
                """,
                rows,
            )
            conn.commit()

    def query_hourly(self, resource_id: str, metric_name: str, start_ts: int, end_ts: int) -> list[dict]:
        """Query hourly data across one or two monthly DBs."""
        start_dt = datetime.utcfromtimestamp(start_ts)
        end_dt = datetime.utcfromtimestamp(end_ts)
        months = []
        y, m = start_dt.year, start_dt.month
        while (y, m) <= (end_dt.year, end_dt.month):
            months.append((y, m))
            m += 1
            if m > 12:
                m = 1
                y += 1

        results = []
        for year, month in months:
            conn = self._raw_conn(year, month)
            cursor = conn.execute(
                """
                SELECT timestamp, value FROM hourly_metrics
                WHERE resource_id = ? AND metric_name = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp
                """,
                (resource_id, metric_name, start_ts, end_ts),
            )
            for row in cursor.fetchall():
                results.append({"timestamp": row[0], "value": row[1]})
        return results

    def downsample_month(self, year: int, month: int) -> int:
        """Aggregate hourly data for a given month into daily_aggregated."""
        conn = self._raw_conn(year, month)
        cursor = conn.execute(
            """
            SELECT resource_id, metric_name, date(timestamp, 'unixepoch') as dt,
                   MIN(value), AVG(value), MAX(value)
            FROM hourly_metrics
            WHERE strftime('%Y-%m', datetime(timestamp, 'unixepoch')) = ?
            GROUP BY resource_id, metric_name, date(timestamp, 'unixepoch')
            ORDER BY resource_id, metric_name, dt
            """,
            (f"{year}-{month:02d}",),
        )
        rows = cursor.fetchall()
        if not rows:
            return 0

        agg_conn = self._agg_conn()
        inserted = 0
        for resource_id, metric_name, dt, min_val, avg_val, max_val in rows:
            # Compute p95 from raw values
            p95_cursor = conn.execute(
                """
                SELECT value FROM hourly_metrics
                WHERE resource_id = ? AND metric_name = ?
                  AND date(timestamp, 'unixepoch') = ?
                ORDER BY value
                """,
                (resource_id, metric_name, dt),
            )
            values = [r[0] for r in p95_cursor.fetchall()]
            p95_val = values[int(len(values) * 0.95)] if values else 0.0

            agg_conn.execute(
                """
                INSERT INTO daily_aggregated (resource_id, metric_name, date, min_value, avg_value, p95_value, max_value, region)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(resource_id, metric_name, date) DO UPDATE SET
                    min_value=excluded.min_value,
                    avg_value=excluded.avg_value,
                    p95_value=excluded.p95_value,
                    max_value=excluded.max_value,
                    region=excluded.region
                """,
                (resource_id, metric_name, dt, min_val, round(avg_val, 2), round(p95_val, 2), max_val, None),
            )
            inserted += 1
        agg_conn.commit()
        return inserted

    def query_daily(self, resource_id: str, metric_name: str, start_date: str, end_date: str) -> list[dict]:
        conn = self._agg_conn()
        cursor = conn.execute(
            """
            SELECT date, min_value, avg_value, p95_value, max_value FROM daily_aggregated
            WHERE resource_id = ? AND metric_name = ? AND date >= ? AND date <= ?
            ORDER BY date
            """,
            (resource_id, metric_name, start_date, end_date),
        )
        return [
            {
                "date": row[0],
                "min_value": row[1],
                "avg_value": row[2],
                "p95_value": row[3],
                "max_value": row[4],
            }
            for row in cursor.fetchall()
        ]

    def cleanup_old_daily(self, keep_days: int = 180) -> int:
        """Delete daily aggregated records older than keep_days."""
        cutoff = (datetime.utcnow() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        conn = self._agg_conn()
        cursor = conn.execute(
            "DELETE FROM daily_aggregated WHERE date < ?",
            (cutoff,),
        )
        conn.commit()
        return cursor.rowcount

    def query_history(self, resource_id: str, metric_name: str, range_label: str) -> dict:
        """Unified history query. range_label: 24h, 7d, 30d, 180d."""
        now = datetime.utcnow()
        if range_label == "24h":
            start = now - timedelta(hours=24)
            granularity = "hourly"
        elif range_label == "7d":
            start = now - timedelta(days=7)
            granularity = "hourly"
        elif range_label == "30d":
            start = now - timedelta(days=30)
            granularity = "hourly"
        elif range_label == "180d":
            start = now - timedelta(days=180)
            granularity = "daily"
        else:
            raise ValueError(f"Unsupported range: {range_label}")

        if granularity == "hourly":
            data = self.query_hourly(
                resource_id, metric_name,
                int(start.timestamp()), int(now.timestamp()),
            )
            values = [d["value"] for d in data if d["value"] is not None]
            stats = self._compute_stats(values)
        else:
            data_raw = self.query_daily(
                resource_id, metric_name,
                start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"),
            )
            data = [{"timestamp": int(datetime.strptime(d["date"], "%Y-%m-%d").timestamp()), **d}
                    for d in data_raw]
            values = [d["avg_value"] for d in data_raw]
            stats = {
                "min": round(min([d["min_value"] for d in data_raw]), 1) if data_raw else None,
                "avg": round(sum(values) / len(values), 1) if values else None,
                "p95": round(sorted([d["p95_value"] for d in data_raw])[int(len(data_raw) * 0.95)], 1) if data_raw else None,
                "max": round(max([d["max_value"] for d in data_raw]), 1) if data_raw else None,
            }

        return {
            "resource_id": resource_id,
            "metric": metric_name,
            "range": range_label,
            "granularity": granularity,
            "data": data,
            "stats": stats,
        }

    @staticmethod
    def _compute_stats(values: list[float]) -> dict:
        if not values:
            return {"min": None, "avg": None, "p95": None, "max": None}
        sorted_vals = sorted(values)
        idx = int(len(sorted_vals) * 0.95)
        p95 = sorted_vals[min(idx, len(sorted_vals) - 1)]
        return {
            "min": round(min(values), 1),
            "avg": round(sum(values) / len(values), 1),
            "p95": round(p95, 1),
            "max": round(max(values), 1),
        }

    def close(self):
        for conn in self._raw_conns.values():
            conn.close()
        self._raw_conns.clear()
        if self._agg_connection:
            self._agg_connection.close()
            self._agg_connection = None
