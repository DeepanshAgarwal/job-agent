"""
agent/scraper/jobspy_scraper.py — LinkedIn + Indeed via JobSpy.

Wraps the open-source python-jobspy library to pull jobs from LinkedIn
and Indeed in a single call, then normalises them into the canonical
job dict format used by the rest of the pipeline.

Speed design
────────────
JobSpy calls are blocking (synchronous HTTP).  We offload each call to
the default ThreadPoolExecutor and cap concurrency with a semaphore so
we never open more than MAX_CONCURRENT simultaneous outbound connections.
Instead of searching every (role, city) pair we search (role, "India")
+ (role, remote) — the `country_indeed` filter already scopes to India.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from agent.scraper.base_scraper import BaseScraper

# Maximum concurrent JobSpy threads (keep low to avoid IP bans).
_MAX_CONCURRENT = 3


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
            from jobspy import scrape_jobs  # type: ignore[import]
        except ImportError:
            logger.warning("JobSpyScraper: jobspy not installed — skipping.")
            return []

        roles: list[str] = preferences.get("roles", ["Software Engineer"])
        locations: list[str] = preferences.get("locations", ["Remote"])
        hours_old: int = preferences.get("posted_within_hours", 24)

        # Build search combinations.  Each city location is searched separately
        # so LinkedIn returns city-scoped results.  "Remote" variants use
        # is_remote=True with an empty location string.
        # country_indeed="india" is always set explicitly — passing a city name
        # as the location would otherwise be misdetected as the country, causing
        # "Invalid country string" errors for city names and "Remote".
        _REMOTE_KEYWORDS = {"remote", "work from home", "wfh"}
        combos: list[tuple[str, str, bool]] = []  # (role, location, is_remote)
        for role in roles:
            for loc in locations:
                if loc.lower() in _REMOTE_KEYWORDS:
                    combos.append((role, "", True))
                else:
                    combos.append((role, loc, False))

        sem = asyncio.Semaphore(_MAX_CONCURRENT)
        loop = asyncio.get_event_loop()

        def _fetch(role: str, location: str, is_remote: bool) -> list[dict[str, Any]]:
            try:
                df = scrape_jobs(
                    site_name=["linkedin", "indeed"],
                    search_term=role,
                    location=location,
                    results_wanted=50,
                    hours_old=hours_old,
                    country_indeed="india",
                    is_remote=is_remote,
                )
                if df is None or df.empty:
                    return []
                return self._normalise(df, hours_old)
            except Exception as exc:  # noqa: BLE001
                label = "remote" if is_remote else location
                logger.error(f"JobSpy error for '{role}' ({label}): {exc}")
                return []

        async def _fetch_async(role: str, location: str, is_remote: bool) -> list[dict[str, Any]]:
            async with sem:
                return await loop.run_in_executor(None, _fetch, role, location, is_remote)

        batch = await asyncio.gather(*[_fetch_async(r, loc, rem) for r, loc, rem in combos])
        jobs: list[dict[str, Any]] = [j for sub in batch for j in sub]

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

            # Build salary string from jobspy columns (min/max + interval)
            min_sal = row.get("min_amount")
            max_sal = row.get("max_amount")
            interval = str(row.get("salary_source") or row.get("interval") or "").upper()
            currency = str(row.get("currency") or "INR")
            if min_sal and max_sal:
                salary_str = f"{currency} {int(min_sal):,}–{int(max_sal):,} {interval}".strip()
            elif min_sal:
                salary_str = f"{currency} {int(min_sal):,}+ {interval}".strip()
            else:
                salary_str = ""

            jobs.append(
                self.build_job(
                    title=str(row.get("title") or ""),
                    company=str(row.get("company") or ""),
                    url=url,
                    source=source,
                    location=str(row.get("location") or ""),
                    description=str(row.get("description") or ""),
                    posted_at=posted_at,
                    salary=salary_str,
                )
            )
        return jobs
