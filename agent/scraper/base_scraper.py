"""
agent/scraper/base_scraper.py — Abstract base class for all scrapers.

Every concrete scraper must inherit from BaseScraper and implement
the ``scrape`` coroutine which returns a list of job dicts.
"""

import hashlib
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class BaseScraper(ABC):
    """Abstract base class that defines the scraper contract."""

    # ── Canonical job dict fields ─────────────────────────────────────────
    # Subclasses should return dicts that include all of these keys.
    JOB_FIELDS = (
        "id",          # str  — stable dedup key (hash of url)
        "title",       # str  — job title
        "company",     # str  — company name
        "location",    # str  — city / "Remote"
        "url",         # str  — canonical application URL
        "description", # str  — full job description text
        "posted_at",   # str  — ISO-8601 datetime string (best effort)
        "source",      # str  — platform name, e.g. "naukri"
    )

    @abstractmethod
    async def scrape(self, preferences: dict) -> list[dict[str, Any]]:
        """Scrape jobs matching *preferences* and return a list of job dicts.

        Each dict must contain at minimum: title, company, url, source.
        All other fields should be populated where available.

        On any error the implementation must log the exception and return
        an empty list — it must *not* raise, so the pipeline continues.

        Args:
            preferences: The preferences dict loaded from preferences.yaml.

        Returns:
            List of job dicts conforming to JOB_FIELDS.
        """

    # ── Helpers available to all subclasses ──────────────────────────────

    @staticmethod
    def make_id(url: str) -> str:
        """Return a short stable ID derived from the job URL."""
        return hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()[:16]

    @staticmethod
    def now_iso() -> str:
        """Return the current UTC time as an ISO-8601 string."""
        return datetime.utcnow().isoformat()

    @staticmethod
    def build_job(
        *,
        title: str,
        company: str,
        url: str,
        source: str,
        location: str = "",
        description: str = "",
        posted_at: str = "",
    ) -> dict[str, Any]:
        """Build a canonical job dict with an auto-generated ID."""
        return {
            "id": BaseScraper.make_id(url),
            "title": title,
            "company": company,
            "location": location,
            "url": url,
            "description": description,
            "posted_at": posted_at or BaseScraper.now_iso(),
            "source": source,
        }
