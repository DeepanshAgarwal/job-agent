"""
agent/tracker/database.py — SQLite persistence layer.

All job tracking data is stored in a local SQLite database at
``data/job_agent.db``.  The database is automatically created and
migrated on first use.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "job_agent.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT,
    company     TEXT NOT NULL,
    role        TEXT NOT NULL,
    url         TEXT NOT NULL UNIQUE,
    source      TEXT,
    match_score REAL,
    status      TEXT DEFAULT 'applied',
    applied_at  TEXT NOT NULL,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS seen_jobs (
    url     TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL
);
"""


class Database:
    """SQLite persistence layer for job application tracking."""

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialise the database wrapper.

        Args:
            db_path: Override the default database path (useful for tests).
        """
        self._path = db_path or _DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        """Return a new SQLite connection with row_factory set."""
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Create tables if they do not already exist."""
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        logger.info(f"Database initialised at {self._path}")

    # ── Dedup helpers ─────────────────────────────────────────────────────

    def is_seen(self, url: str) -> bool:
        """Return True if *url* has already been seen (scraped or applied).

        Args:
            url: Canonical job application URL.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_jobs WHERE url = ? LIMIT 1", (url,)
            ).fetchone()
            return row is not None

    def mark_seen(self, job: dict[str, Any]) -> None:
        """Record *job* URL as seen so it will be skipped next run.

        Args:
            job: Job dict containing at least a ``url`` key.
        """
        url = job.get("url", "")
        if not url:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_jobs (url, seen_at) VALUES (?, ?)",
                (url, datetime.utcnow().isoformat()),
            )

    # ── Application logging ───────────────────────────────────────────────

    def log_application(self, job: dict[str, Any]) -> None:
        """Insert a submitted application record into the database.

        Args:
            job: Job dict; must contain ``url``, ``company``, ``title``.
        """
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO applications
                        (job_id, company, role, url, source, match_score, status, applied_at, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.get("id"),
                        job.get("company", ""),
                        job.get("title", ""),
                        job.get("url", ""),
                        job.get("source", ""),
                        job.get("match_score"),
                        job.get("status", "applied"),
                        datetime.utcnow().isoformat(),
                        job.get("notes", ""),
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"DB.log_application failed: {exc}")

    # ── Stats ─────────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Return aggregated application statistics.

        Returns:
            Dict with keys: total_applied, by_platform (dict), by_status (dict).
        """
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]

            by_platform: dict[str, int] = {}
            for row in conn.execute(
                "SELECT source, COUNT(*) as cnt FROM applications GROUP BY source"
            ).fetchall():
                by_platform[row["source"] or "unknown"] = row["cnt"]

            by_status: dict[str, int] = {}
            for row in conn.execute(
                "SELECT status, COUNT(*) as cnt FROM applications GROUP BY status"
            ).fetchall():
                by_status[row["status"] or "unknown"] = row["cnt"]

        return {
            "total_applied": total,
            "by_platform": by_platform,
            "by_status": by_status,
        }

    def get_recent_applications(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent *limit* application records.

        Args:
            limit: Maximum number of rows to return.

        Returns:
            List of dicts representing each application row.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM applications ORDER BY applied_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]
