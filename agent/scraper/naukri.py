"""
agent/scraper/naukri.py — Playwright-based scraper for Naukri.com.

Logs in using a saved browser session, searches for jobs by role and
location, extracts job cards across up to 3 pages of results, and
returns a list of canonical job dicts.

Naukri is a Next.js SSR app — content is available at domcontentloaded.
Job links follow the pattern /job-listings-{slug}.
"""

import re
from typing import Any

from loguru import logger

from agent.scraper.base_scraper import BaseScraper
from agent.submitter.session_manager import SessionManager

_BASE_URL = "https://www.naukri.com"
# Known card container selectors in order of likelihood
_CARD_SELECTORS = [
    "div.cust-job-tuple",
    "article.jobTuple",
    "div[class*='jobTuple']",
    "div[class*='job-tuple']",
]
# Naukri job detail links always contain /job-listings-
_JOB_LINK_RE = re.compile(r"/job-listings-")


class NaukriScraper(BaseScraper):
    """Scraper for Naukri.com using Playwright + saved session."""

    async def scrape(self, preferences: dict) -> list[dict[str, Any]]:
        """Scrape job listings from Naukri.com.

        Args:
            preferences: Loaded preferences.yaml dict.

        Returns:
            List of canonical job dicts; empty list on any failure.
        """
        roles: list[str] = preferences.get("roles", ["Software Engineer"])
        locations: list[str] = preferences.get("locations", ["Bangalore"])
        hours_old: int = preferences.get("posted_within_hours", 24)

        jobs: list[dict[str, Any]] = []

        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ImportError:
            logger.warning("NaukriScraper: playwright not installed — skipping.")
            return jobs

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            session_mgr = SessionManager()
            context = await session_mgr.load_session("naukri", browser)

            for role in roles:
                for location in locations:
                    try:
                        page_jobs = await self._scrape_search(
                            context, role, location, hours_old
                        )
                        jobs.extend(page_jobs)
                    except Exception as exc:  # noqa: BLE001
                        logger.error(f"NaukriScraper error for '{role}'/'{location}': {exc}")

            await context.close()
            await browser.close()

        # De-duplicate
        seen: set[str] = set()
        unique = []
        for j in jobs:
            if j["url"] not in seen:
                seen.add(j["url"])
                unique.append(j)

        logger.info(f"NaukriScraper: {len(unique)} unique jobs found")
        return unique

    async def _scrape_search(
        self, context: Any, role: str, location: str, hours_old: int
    ) -> list[dict[str, Any]]:
        """Navigate to search results and extract jobs.

        Uses query_selector_all (non-waiting element handles) to avoid
        Playwright locator timeouts. Falls back to link harvesting if
        no known card selector matches.
        """
        page = await context.new_page()
        role_slug = role.lower().replace(" ", "-")

        if location.lower() == "remote":
            url = f"{_BASE_URL}/{role_slug}-jobs?workfromhome=1"
        else:
            location_slug = location.lower().replace(" ", "-")
            url = f"{_BASE_URL}/{role_slug}-jobs-in-{location_slug}"

        jobs: list[dict[str, Any]] = []

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"NaukriScraper: goto failed for '{role}'/'{location}': {exc}")
            await page.close()
            return jobs

        # --- Primary: try known card container selectors ---
        cards = []
        for sel in _CARD_SELECTORS:
            cards = await page.query_selector_all(sel)
            if cards:
                break

        if cards:
            for card in cards:
                try:
                    link_el = await card.query_selector("a[href*='/job-listings-']")
                    if link_el is None:
                        continue
                    title = (await link_el.inner_text()).strip()
                    href = await link_el.get_attribute("href") or ""
                    job_url = href if href.startswith("http") else _BASE_URL + href

                    company_el = await card.query_selector(
                        "a.subTitle, a[href*='/jobs-careers-'], .comp-name"
                    )
                    company = (await company_el.inner_text()).strip() if company_el else "Unknown"

                    loc_el = await card.query_selector(
                        "li.fleft, span.locWdth, .loc, [class*='location']"
                    )
                    location_text = (await loc_el.inner_text()).strip() if loc_el else location

                    jobs.append(
                        self.build_job(
                            title=title,
                            company=company,
                            url=job_url,
                            source="naukri",
                            location=location_text,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"NaukriScraper: card parse error: {exc}")

        # --- Fallback: harvest job links directly ---
        if not jobs:
            links = await page.query_selector_all("a[href*='/job-listings-']")
            for link in links:
                try:
                    href = await link.get_attribute("href") or ""
                    if not _JOB_LINK_RE.search(href):
                        continue
                    job_url = href if href.startswith("http") else _BASE_URL + href
                    title = (await link.inner_text()).strip()
                    if not title:
                        continue
                    jobs.append(
                        self.build_job(
                            title=title,
                            company="Unknown",
                            url=job_url,
                            source="naukri",
                            location=location,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"NaukriScraper: link parse error: {exc}")

        await page.close()
        return jobs
