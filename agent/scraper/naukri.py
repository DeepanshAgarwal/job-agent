"""
agent/scraper/naukri.py — Playwright-based scraper for Naukri.com.

Logs in using a saved browser session, searches for jobs by role and
location, extracts job cards across up to 3 pages of results, and
returns a list of canonical job dicts.

TODO: Verify CSS selectors against live Naukri.com before first run.
"""

from typing import Any

from loguru import logger

from agent.scraper.base_scraper import BaseScraper
from agent.submitter.session_manager import SessionManager

# TODO: Verify these selectors on live Naukri.com
_SELECTORS = {
    "job_card": "article.jobTuple",
    "title": "a.title",
    "company": "a.subTitle",
    "location": "li.fleft.br2.bl",
    "posted": "span.fw500",
    "next_page": "a.fright.fs14.btn-secondary.next-btn",
}

_BASE_URL = "https://www.naukri.com"
_SEARCH_URL = "https://www.naukri.com/{role}-jobs-in-{location}"


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
            logger.warning("playwright not installed — skipping Naukri scraping.")
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
        """Navigate to search results and extract jobs across pages."""
        page = await context.new_page()
        role_slug = role.lower().replace(" ", "-")
        location_slug = location.lower().replace(" ", "-")
        url = _SEARCH_URL.format(role=role_slug, location=location_slug)

        jobs: list[dict[str, Any]] = []

        for page_num in range(1, 4):  # max 3 pages
            if page_num == 1:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            else:
                next_btn = page.locator(_SELECTORS["next_page"])
                if not await next_btn.is_visible():
                    break
                await next_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=15_000)

            cards = page.locator(_SELECTORS["job_card"])
            count = await cards.count()
            for i in range(count):
                try:
                    card = cards.nth(i)
                    title = await card.locator(_SELECTORS["title"]).inner_text()
                    company = await card.locator(_SELECTORS["company"]).first.inner_text()
                    loc_text = await card.locator(_SELECTORS["location"]).first.inner_text()
                    # TODO: parse posted date and filter by hours_old
                    href = await card.locator(_SELECTORS["title"]).get_attribute("href")
                    job_url = href if href and href.startswith("http") else _BASE_URL + (href or "")
                    jobs.append(
                        self.build_job(
                            title=title.strip(),
                            company=company.strip(),
                            url=job_url,
                            source="naukri",
                            location=loc_text.strip(),
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"Naukri card parse error: {exc}")

        await page.close()
        return jobs
