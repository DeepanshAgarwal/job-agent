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

CREATE TABLE IF NOT EXISTS scraped_jobs (
    url                 TEXT PRIMARY KEY,
    title               TEXT,
    company             TEXT,
    location            TEXT,
    source              TEXT,
    scraped_at          TEXT NOT NULL,
    match_score         REAL,
    outcome             TEXT DEFAULT 'pending',
    -- outcome: pending | low_score | ai_rejected | shortlisted | applied | error
    scored_at           TEXT,
    notes               TEXT,
    experience_required TEXT,
    salary              TEXT,
    -- Gemini-specific fields (populated after AI analysis)
    gemini_score        REAL,      -- Gemini's own match_score (0-100)
    gemini_reasons      TEXT,      -- JSON array of match_reasons
    gemini_decided_at   TEXT       -- ISO timestamp of Gemini decision
);
"""

# Each string is attempted as an ALTER TABLE; failures (column exists) are silently ignored.
_MIGRATIONS: list[str] = [
    "ALTER TABLE scraped_jobs ADD COLUMN notes TEXT;",
    "ALTER TABLE scraped_jobs ADD COLUMN experience_required TEXT;",
    "ALTER TABLE scraped_jobs ADD COLUMN salary TEXT;",
    "ALTER TABLE scraped_jobs ADD COLUMN gemini_score REAL;",
    "ALTER TABLE scraped_jobs ADD COLUMN gemini_reasons TEXT;",
    "ALTER TABLE scraped_jobs ADD COLUMN gemini_decided_at TEXT;",
]


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
        """Create tables and apply any pending column migrations."""
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            for stmt in _MIGRATIONS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # column already exists
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

    # ── Scraped-job tracking ──────────────────────────────────────────────

    def upsert_scraped_jobs(self, jobs: list[dict[str, Any]]) -> None:
        """Insert newly scraped jobs; ignore duplicates already in the table.

        Args:
            jobs: List of job dicts from scrapers.
        """
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO scraped_jobs
                    (url, title, company, location, source, scraped_at, outcome,
                     experience_required, salary)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    scraped_at = excluded.scraped_at,
                    title      = excluded.title,
                    company    = excluded.company,
                    location   = excluded.location,
                    source     = excluded.source
                """,
                [
                    (
                        j.get("url", ""),
                        j.get("title", ""),
                        j.get("company", ""),
                        j.get("location", ""),
                        j.get("source", ""),
                        now,
                        j.get("experience_required"),
                        j.get("salary"),
                    )
                    for j in jobs
                    if j.get("url")
                ],
            )

    def update_job_score(self, url: str, score: float) -> None:
        """Attach the embedding match score to a scraped job row.

        Args:
            url: Job URL (primary key).
            score: Float 0–100 from the sentence-transformer matcher.
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE scraped_jobs SET match_score = ?, scored_at = ? WHERE url = ?",
                (score, datetime.utcnow().isoformat(), url),
            )

    def update_job_outcome(
        self,
        url: str,
        outcome: str,
        notes: str = "",
        gemini_score: float | None = None,
        gemini_reasons: list[str] | None = None,
    ) -> None:
        """Record the pipeline decision for a scraped job.

        Args:
            url: Job URL (primary key).
            outcome: One of 'low_score', 'ai_rejected', 'shortlisted', 'applied', 'error'.
            notes: Human-readable reason string (skip_reason or match_reasons summary).
            gemini_score: Gemini's own 0-100 match score.
            gemini_reasons: List of reasons Gemini returned.
        """
        import json as _json
        now = datetime.utcnow().isoformat() if gemini_score is not None or gemini_reasons is not None else None
        reasons_json = _json.dumps(gemini_reasons) if gemini_reasons is not None else None
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE scraped_jobs
                SET outcome           = ?,
                    notes             = ?,
                    gemini_score      = COALESCE(?, gemini_score),
                    gemini_reasons    = COALESCE(?, gemini_reasons),
                    gemini_decided_at = COALESCE(?, gemini_decided_at)
                WHERE url = ?
                """,
                (outcome, notes, gemini_score, reasons_json, now, url),
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
