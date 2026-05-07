"""
agent/scraper/jobspy_scraper.py — LinkedIn + Indeed via JobSpy.

Wraps the open-source python-jobspy library to pull jobs from LinkedIn
and Indeed in a single call, then normalises them into the canonical
job dict format used by the rest of the pipeline.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from agent.scraper.base_scraper import BaseScraper


class JobSpyScraper(BaseScraper):
    """Scraper backed by the JobSpy library (LinkedIn + Indeed)."""

    async def scrape(self, preferences: dict) -> list[dict[str, Any]]:
        """Return job listings from LinkedIn and Indeed.

        Args:
            preferences: Loaded preferences.yaml dict.

        Returns:
            List of normalised job dicts.
        """
        try:
            # Import lazily so the rest of the app works even if jobspy is
            # not installed (e.g. in CI where we test other modules).
            from jobspy import scrape_jobs  # type: ignore[import]
        except ImportError:
            logger.warning("jobspy not installed — skipping LinkedIn/Indeed scraping.")
            return []

        roles: list[str] = preferences.get("roles", ["Software Engineer"])
        locations: list[str] = preferences.get("locations", ["Remote"])
        hours_old: int = preferences.get("posted_within_hours", 24)

        jobs: list[dict[str, Any]] = []

        for role in roles:
            for location in locations:
                try:
                    df = scrape_jobs(
                        site_name=["linkedin", "indeed"],
                        search_term=role,
                        location=location,
                        results_wanted=50,
                        hours_old=hours_old,
                    )
                    if df is None or df.empty:
                        continue
                    jobs.extend(self._normalise(df, hours_old))
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"JobSpy error for '{role}' in '{location}': {exc}")

        # De-duplicate within this batch by URL
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for job in jobs:
            if job["url"] not in seen:
                seen.add(job["url"])
                unique.append(job)

        logger.info(f"JobSpyScraper: {len(unique)} unique jobs found")
        return unique

    def _normalise(self, df: Any, hours_old: int) -> list[dict[str, Any]]:
        """Convert a JobSpy DataFrame to a list of canonical job dicts."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours_old)
        jobs: list[dict[str, Any]] = []

        for _, row in df.iterrows():
            url = str(row.get("job_url") or row.get("url") or "")
            if not url:
                continue

            # Parse posted_at — jobspy returns a datetime or string
            raw_date = row.get("date_posted") or row.get("posted_at")
            posted_at: str = ""
            if raw_date:
                try:
                    if isinstance(raw_date, datetime):
                        dt = raw_date if raw_date.tzinfo else raw_date.replace(tzinfo=timezone.utc)
                    else:
                        dt = datetime.fromisoformat(str(raw_date)).replace(tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue  # too old
                    posted_at = dt.isoformat()
                except ValueError:
                    posted_at = str(raw_date)

            source = str(row.get("site") or "jobspy").lower()
            jobs.append(
                self.build_job(
                    title=str(row.get("title") or ""),
                    company=str(row.get("company") or ""),
                    url=url,
                    source=source,
                    location=str(row.get("location") or ""),
                    description=str(row.get("description") or ""),
                    posted_at=posted_at,
                )
            )
        return jobs
