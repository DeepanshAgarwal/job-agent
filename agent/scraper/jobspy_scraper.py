"""
agent/scraper/jobspy_scraper.py — Base class for JobSpy-backed scrapers.

Set `_SITE = "linkedin"` or `_SITE = "indeed"` in a subclass.
See linkedin.py and indeed.py for the concrete scrapers.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from agent.scraper.base_scraper import BaseScraper

_IST = ZoneInfo("Asia/Kolkata")

# Maximum concurrent JobSpy threads (keep low to avoid IP bans).
_MAX_CONCURRENT = 3


class JobSpyScraper(BaseScraper):
    """Base scraper backed by the JobSpy library. Subclass and set _SITE."""

    _SITE: str = ""  # "linkedin" or "indeed" — must be set by subclasses

    async def scrape(self, preferences: dict, on_search_done: Any = None) -> list[dict[str, Any]]:
        if not self._SITE:
            raise NotImplementedError(f"{self.__class__.__name__}: _SITE must be set")

        roles: list[str] = preferences.get("roles", ["Software Engineer"])
        locations: list[str] = preferences.get("locations", ["Remote"])
        hours_old: int = preferences.get("posted_within_hours", 24)

        _REMOTE_KEYWORDS = {"remote", "work from home", "wfh"}
        combos: list[tuple[str, str, bool]] = []  # (role, location, is_remote)
        for role in roles:
            for loc in locations:
                if loc.lower() in _REMOTE_KEYWORDS:
                    combos.append((role, "", True))
                else:
                    combos.append((role, loc, False))

        site = self._SITE
        sem = asyncio.Semaphore(_MAX_CONCURRENT)
        loop = asyncio.get_event_loop()

        def _fetch(role: str, location: str, is_remote: bool) -> list[dict[str, Any]]:
            try:
                from jobspy import scrape_jobs  # type: ignore[import]  # noqa: PLC0415
                import logging as _log  # noqa: PLC0415
                # scrape_jobs re-registers its own handlers during execution so
                # pre-call suppression alone doesn't work.  Disable INFO globally
                # for the duration of the call to silence "finished scraping" noise.
                _log.disable(_log.INFO)
                try:
                    df = scrape_jobs(
                        site_name=[site],
                        search_term=role,
                        location=location,
                        results_wanted=50,
                        hours_old=hours_old,
                        country_indeed="india",
                        is_remote=is_remote,
                    )
                finally:
                    _log.disable(_log.NOTSET)
                if df is None or df.empty:
                    return []
                return self._normalise(df, hours_old)
            except Exception as exc:  # noqa: BLE001
                label = "remote" if is_remote else location
                logger.error(f"{self.__class__.__name__} error for '{role}' ({label}): {exc}")
                return []

        async def _fetch_async(role: str, location: str, is_remote: bool) -> list[dict[str, Any]]:
            async with sem:
                result = await loop.run_in_executor(None, _fetch, role, location, is_remote)
            if on_search_done:
                on_search_done()
            return result

        batch = await asyncio.gather(*[_fetch_async(r, loc, rem) for r, loc, rem in combos])
        jobs: list[dict[str, Any]] = [j for sub in batch for j in sub]

        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for job in jobs:
            if job["url"] not in seen:
                seen.add(job["url"])
                unique.append(job)

        logger.info(f"{self.__class__.__name__}: {len(unique)} unique jobs found")
        return unique

    def _normalise(self, df: Any, hours_old: int) -> list[dict[str, Any]]:
        """Convert a JobSpy DataFrame to a list of canonical job dicts."""
        cutoff = datetime.now(tz=_IST) - timedelta(hours=hours_old)
        jobs: list[dict[str, Any]] = []

        for _, row in df.iterrows():
            url = str(row.get("job_url") or row.get("url") or "")
            if not url:
                continue

            # For Indeed, prefer job_url_direct (the company's actual ATS page)
            # over the Indeed listing URL.  This lets form_filler correctly detect
            # and route to Greenhouse/Lever/etc. instead of always using generic.
            direct_url = str(row.get("job_url_direct") or "")
            _BOARD_DOMAINS = ("indeed.com", "linkedin.com", "glassdoor.com")
            if direct_url and not any(d in direct_url for d in _BOARD_DOMAINS):
                url = direct_url

            # Parse posted_at — jobspy returns a datetime or string
            raw_date = row.get("date_posted") or row.get("posted_at")
            posted_at: str = ""
            if raw_date:
                try:
                    if isinstance(raw_date, datetime):
                        dt = raw_date.astimezone(_IST) if raw_date.tzinfo else raw_date.replace(tzinfo=_IST)
                    else:
                        from datetime import timezone  # noqa: PLC0415
                        dt = datetime.fromisoformat(str(raw_date)).replace(tzinfo=timezone.utc).astimezone(_IST)
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
                    description=str(row.get("description") or "") or (
                        f"{row.get('title', '')} at {row.get('company', '')}, {row.get('location', '')}"
                        if source == "linkedin"
                        else ""
                    ),
                    posted_at=posted_at,
                    salary=salary_str,
                )
            )
        return jobs

