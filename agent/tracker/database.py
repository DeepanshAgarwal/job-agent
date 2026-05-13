"""
agent/tracker/database.py — SQLite persistence layer.

Schema (v2 — session-based, append-only audit trail):

  sessions      — one row per pipeline run (created at startup)
  jobs          — canonical job records; one row per URL, never overwritten
  session_jobs  — per-job, per-run ledger (append-only, full audit trail)
  seen_jobs     — global dedup gate (independent of sessions)

Outcome values for session_jobs.outcome:
  pending → shortlisted → applied
                        → failed
                        → error
         → low_score
         → ai_rejected

Legacy tables (scraped_jobs, applications) are preserved so existing DBs
are not broken.  New code writes only to the v2 tables.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

_IST = ZoneInfo("Asia/Kolkata")

def _now() -> str:
    """Current IST time as ISO-8601 string."""
    return datetime.now(tz=_IST).isoformat()

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "job_agent.db"

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA_V2 = """
-- ── sessions ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY,   -- ISO timestamp "2026-05-10T14:32:05"
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    resume_hash       TEXT,               -- sha256 of resume file
    prefs_hash        TEXT,               -- sha256 of preferences.yaml
    total_scraped     INTEGER DEFAULT 0,
    total_scored      INTEGER DEFAULT 0,
    total_gemini      INTEGER DEFAULT 0,
    total_shortlisted INTEGER DEFAULT 0,
    total_applied     INTEGER DEFAULT 0,
    total_failed      INTEGER DEFAULT 0
);

-- ── jobs ──────────────────────────────────────────────────────────────────────
-- Canonical job metadata. Populated on first scrape, never overwritten.
CREATE TABLE IF NOT EXISTS jobs (
    url                 TEXT PRIMARY KEY,
    title               TEXT,
    company             TEXT,
    location            TEXT,
    source              TEXT,
    experience_required TEXT,
    salary              TEXT,
    description         TEXT,
    first_seen_at       TEXT NOT NULL
);

-- ── session_jobs ──────────────────────────────────────────────────────────────
-- Append-only per-run ledger — every pipeline decision is recorded here.
CREATE TABLE IF NOT EXISTS session_jobs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL REFERENCES sessions(id),
    url               TEXT NOT NULL REFERENCES jobs(url),
    embedding_score   REAL,
    gemini_score      REAL,
    gemini_reasons    TEXT,       -- JSON array
    gemini_decision   TEXT,       -- "apply" | "reject" | null
    outcome           TEXT NOT NULL DEFAULT 'pending',
    -- outcome: pending | low_score | deferred | ai_rejected | shortlisted | applied | failed | error
    failure_reason    TEXT,
    cover_letter      TEXT,
    scored_at         TEXT,
    gemini_decided_at TEXT,
    applied_at        TEXT,
    UNIQUE(session_id, url)
);
CREATE INDEX IF NOT EXISTS idx_sj_session ON session_jobs(session_id);
CREATE INDEX IF NOT EXISTS idx_sj_url     ON session_jobs(url);
CREATE INDEX IF NOT EXISTS idx_sj_outcome ON session_jobs(outcome);

-- ── seen_jobs ─────────────────────────────────────────────────────────────────
-- Global dedup gate — a URL here is never re-submitted.
CREATE TABLE IF NOT EXISTS seen_jobs (
    url        TEXT PRIMARY KEY,
    seen_at    TEXT NOT NULL,
    session_id TEXT
);

-- ── Legacy tables (v1) — not written by new code, kept for safety ─────────────
CREATE TABLE IF NOT EXISTS scraped_jobs (
    url                 TEXT PRIMARY KEY,
    title               TEXT,
    company             TEXT,
    location            TEXT,
    source              TEXT,
    scraped_at          TEXT NOT NULL,
    match_score         REAL,
    outcome             TEXT DEFAULT 'pending',
    scored_at           TEXT,
    notes               TEXT,
    experience_required TEXT,
    salary              TEXT,
    gemini_score        REAL,
    gemini_reasons      TEXT,
    gemini_decided_at   TEXT
);

CREATE TABLE IF NOT EXISTS applications (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         TEXT,
    company        TEXT NOT NULL,
    role           TEXT NOT NULL,
    url            TEXT NOT NULL UNIQUE,
    source         TEXT,
    match_score    REAL,
    gemini_score   REAL,
    gemini_reasons TEXT,
    status         TEXT DEFAULT 'applied',
    applied_at     TEXT NOT NULL,
    notes          TEXT
);
"""


class Database:
    """SQLite persistence layer for job application tracking."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = db_path or _DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self) -> None:
        """Create all tables (idempotent — safe to call every run)."""
        with self._connect() as conn:
            conn.executescript(_SCHEMA_V2)
        logger.info(f"Database ready at {self._path}")

    # ── Session management ────────────────────────────────────────────────

    def create_session(self, resume_hash: str = "", prefs_hash: str = "") -> str:
        """Create a new session row and return its ID (ISO timestamp string)."""
        session_id = datetime.now(tz=_IST).strftime("%Y-%m-%dT%H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, started_at, resume_hash, prefs_hash) "
                "VALUES (?, ?, ?, ?)",
                (session_id, _now(), resume_hash, prefs_hash),
            )
        logger.info(f"Session started: {session_id}")
        return session_id

    def finish_session(self, session_id: str, counts: dict[str, int]) -> None:
        """Record final counts and finished_at timestamp for the session."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sessions SET
                    finished_at       = ?,
                    total_scraped     = ?,
                    total_scored      = ?,
                    total_gemini      = ?,
                    total_shortlisted = ?,
                    total_applied     = ?,
                    total_failed      = ?
                WHERE id = ?
                """,
                (
                    _now(),
                    counts.get("total_scraped", 0),
                    counts.get("total_scored", 0),
                    counts.get("total_gemini", 0),
                    counts.get("total_shortlisted", 0),
                    counts.get("total_applied", 0),
                    counts.get("total_failed", 0),
                    session_id,
                ),
            )

    # ── Canonical job table ───────────────────────────────────────────────

    def upsert_jobs(self, jobs: list[dict[str, Any]]) -> None:
        """Insert new jobs; ignore duplicates (canonical data never overwritten)."""
        now = _now()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO jobs "
                "(url, title, company, location, source, experience_required, salary, description, first_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        j["url"],
                        j.get("title", ""),
                        j.get("company", ""),
                        j.get("location", ""),
                        j.get("source", ""),
                        j.get("experience_required"),
                        j.get("salary"),
                        j.get("description", ""),
                        now,
                    )
                    for j in jobs
                    if j.get("url")
                ],
            )

    # ── session_jobs helpers ──────────────────────────────────────────────

    def _ensure_session_job(self, session_id: str, url: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO session_jobs (session_id, url) VALUES (?, ?)",
                (session_id, url),
            )

    def record_embedding_score(self, session_id: str, url: str, score: float) -> None:
        """Store the embedding match score for a job in this session."""
        self._ensure_session_job(session_id, url)
        with self._connect() as conn:
            conn.execute(
                "UPDATE session_jobs SET embedding_score = ?, scored_at = ? "
                "WHERE session_id = ? AND url = ?",
                (score, _now(), session_id, url),
            )

    def record_gemini_decision(
        self,
        session_id: str,
        url: str,
        *,
        gemini_score: float | None,
        gemini_reasons: list[str] | None,
        decision: str,
        outcome: str,
        failure_reason: str = "",
    ) -> None:
        """Store Gemini analysis results for a job in this session."""
        self._ensure_session_job(session_id, url)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE session_jobs
                SET gemini_score      = ?,
                    gemini_reasons    = ?,
                    gemini_decision   = ?,
                    outcome           = ?,
                    failure_reason    = COALESCE(NULLIF(?, ''), failure_reason),
                    gemini_decided_at = ?
                WHERE session_id = ? AND url = ?
                """,
                (
                    gemini_score,
                    json.dumps(gemini_reasons or []),
                    decision,
                    outcome,
                    failure_reason,
                    _now(),
                    session_id,
                    url,
                ),
            )

    def record_outcome(
        self,
        session_id: str,
        url: str,
        outcome: str,
        failure_reason: str = "",
        cover_letter: str = "",
    ) -> None:
        """Record the final apply outcome for a job in this session."""
        self._ensure_session_job(session_id, url)
        applied_at = _now() if outcome == "applied" else None
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE session_jobs
                SET outcome        = ?,
                    failure_reason = COALESCE(NULLIF(?, ''), failure_reason),
                    cover_letter   = COALESCE(NULLIF(?, ''), cover_letter),
                    applied_at     = COALESCE(?, applied_at)
                WHERE session_id = ? AND url = ?
                """,
                (outcome, failure_reason, cover_letter, applied_at, session_id, url),
            )

    def set_outcome_bulk(self, session_id: str, urls: list[str], outcome: str) -> None:
        """Set the same outcome tag for multiple jobs in one transaction."""
        now = _now()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO session_jobs (session_id, url) VALUES (?, ?)",
                [(session_id, url) for url in urls],
            )
            conn.executemany(
                "UPDATE session_jobs SET outcome = ?, scored_at = ? WHERE session_id = ? AND url = ?",
                [(outcome, now, session_id, url) for url in urls],
            )

    # ── Dedup helpers ─────────────────────────────────────────────────────

    def is_seen(self, url: str) -> bool:
        """Return True if this URL is in the global seen_jobs gate."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM seen_jobs WHERE url = ? LIMIT 1", (url,)
            ).fetchone() is not None

    def mark_seen(self, job: dict[str, Any], session_id: str = "") -> None:
        """Record a job URL as globally seen (prevents future re-submission)."""
        url = job.get("url", "")
        if not url:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_jobs (url, seen_at, session_id) VALUES (?, ?, ?)",
                (url, _now(), session_id or None),
            )

    # ── Query helpers ─────────────────────────────────────────────────────

    def get_retry_jobs(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """Return jobs that failed/errored or were shortlisted-but-not-applied.

        If *session_id* is None, uses the most recent session.
        Returns full job dicts with scoring fields attached (same shape as scraper output).
        """
        with self._connect() as conn:
            if session_id is None:
                row = conn.execute(
                    "SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
                if row is None:
                    return []
                session_id = row["id"]

            rows = conn.execute(
                """
                SELECT j.*, sj.embedding_score AS match_score,
                       sj.gemini_score, sj.gemini_reasons AS match_reasons,
                       sj.outcome, sj.failure_reason AS notes, sj.session_id
                FROM session_jobs sj
                JOIN jobs j ON j.url = sj.url
                WHERE sj.session_id = ?
                  AND sj.outcome IN ('failed', 'error', 'shortlisted')
                ORDER BY sj.embedding_score DESC
                """,
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_stats(self) -> dict[str, Any]:
        """Return statistics from the most recent session."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row:
                session_dict = dict(row)
                by_outcome: dict[str, int] = {}
                for r in conn.execute(
                    "SELECT outcome, COUNT(*) AS cnt FROM session_jobs "
                    "WHERE session_id = ? GROUP BY outcome",
                    (session_dict["id"],),
                ).fetchall():
                    by_outcome[r["outcome"]] = r["cnt"]
                total_applied_all = conn.execute(
                    "SELECT COUNT(*) FROM session_jobs WHERE outcome = 'applied'"
                ).fetchone()[0]
                return {
                    "session_id": session_dict["id"],
                    "total_applied": session_dict.get("total_applied", 0),
                    "total_applied_all_time": total_applied_all,
                    "by_outcome": by_outcome,
                    "total_scraped": session_dict.get("total_scraped", 0),
                    "total_shortlisted": session_dict.get("total_shortlisted", 0),
                }
            # Legacy fallback for DBs that have no sessions yet
            total = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
            return {"total_applied": total, "by_outcome": {}, "total_scraped": 0}

    def get_recent_applications(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent *limit* applied jobs across all sessions."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT j.url, j.title, j.company, j.location, j.source,
                       sj.embedding_score, sj.gemini_score, sj.gemini_reasons,
                       sj.outcome, sj.failure_reason, sj.applied_at, sj.session_id
                FROM session_jobs sj
                JOIN jobs j ON j.url = sj.url
                WHERE sj.outcome = 'applied'
                ORDER BY sj.applied_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Backward-compat shims ─────────────────────────────────────────────
    # Old call-sites in main.py still use these signatures.
    # They delegate to the new methods when session_id is provided,
    # or fall back to the legacy tables when it isn't.

    def upsert_scraped_jobs(self, jobs: list[dict[str, Any]]) -> None:
        self.upsert_jobs(jobs)

    def update_job_score(self, url: str, score: float, session_id: str = "") -> None:
        if session_id:
            self.record_embedding_score(session_id, url, score)

    def update_job_outcome(
        self,
        url: str,
        outcome: str,
        notes: str = "",
        gemini_score: float | None = None,
        gemini_reasons: list[str] | None = None,
        session_id: str = "",
    ) -> None:
        if session_id:
            self.record_outcome(session_id, url, outcome, failure_reason=notes)

    def log_application(self, job: dict[str, Any], session_id: str = "") -> None:
        """Write to legacy applications table (still used by --status view)."""
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO applications "
                    "(job_id, company, role, url, source, match_score, gemini_score, "
                    "gemini_reasons, status, applied_at, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job.get("id"),
                        job.get("company", ""),
                        job.get("title", ""),
                        job.get("url", ""),
                        job.get("source", ""),
                        job.get("match_score"),
                        job.get("gemini_score"),
                        json.dumps(job.get("match_reasons") or []),
                        job.get("status", "applied"),
                        _now(),
                        job.get("notes", ""),
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"DB.log_application failed: {exc}")
