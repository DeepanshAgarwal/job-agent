"""
agent/scraper/instahyre.py — Playwright-based scraper for Instahyre.com.

Instahyre is a tech-focused platform well-suited for senior engineering
roles. This scraper logs in via a saved session, searches by role and
location, and extracts job cards.

TODO: Verify CSS selectors against live Instahyre.com before first run.
"""

from typing import Any

from loguru import logger

from agent.scraper.base_scraper import BaseScraper
from agent.submitter.session_manager import SessionManager

# TODO: Verify selectors on live Instahyre.com
_SELECTORS = {
    "job_card": "div.job-card",
    "title": "h2.job-title",
    "company": "span.company-name",
    "location": "span.location",
    "link": "a.job-card-link",
}

_BASE_URL = "https://www.instahyre.com"
_SEARCH_URL = "https://www.instahyre.com/search-jobs/?q={role}&l={location}"


class InstaHyreScraper(BaseScraper):
    """Scraper for Instahyre.com using Playwright + saved session."""

    async def scrape(self, preferences: dict) -> list[dict[str, Any]]:
        """Scrape job listings from Instahyre.com.

        Args:
            preferences: Loaded preferences.yaml dict.

        Returns:
            List of canonical job dicts; empty list on any failure.
        """
        roles: list[str] = preferences.get("roles", ["Software Engineer"])
        locations: list[str] = preferences.get("locations", ["Bangalore"])

        jobs: list[dict[str, Any]] = []

        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ImportError:
            logger.warning("playwright not installed — skipping InstaHyre scraping.")
            return jobs

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            session_mgr = SessionManager()
            context = await session_mgr.load_session("instahyre", browser)

            for role in roles:
                for location in locations:
                    try:
                        page_jobs = await self._scrape_search(context, role, location)
                        jobs.extend(page_jobs)
                    except Exception as exc:  # noqa: BLE001
                        logger.error(f"InstaHyreScraper error for '{role}'/'{location}': {exc}")

            await context.close()
            await browser.close()

        seen: set[str] = set()
        unique = [j for j in jobs if not (j["url"] in seen or seen.add(j["url"]))]
        logger.info(f"InstaHyreScraper: {len(unique)} unique jobs found")
        return unique

    async def _scrape_search(self, context: Any, role: str, location: str) -> list[dict[str, Any]]:
        """Navigate search results and extract job listings."""
        page = await context.new_page()
        url = _SEARCH_URL.format(role=role.replace(" ", "+"), location=location.replace(" ", "+"))
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        jobs: list[dict[str, Any]] = []
        cards = page.locator(_SELECTORS["job_card"])
        count = await cards.count()

        for i in range(count):
            try:
                card = cards.nth(i)
                title = await card.locator(_SELECTORS["title"]).inner_text()
                company = await card.locator(_SELECTORS["company"]).inner_text()
                loc_text = await card.locator(_SELECTORS["location"]).inner_text()
                href = await card.locator(_SELECTORS["link"]).get_attribute("href")
                job_url = href if href and href.startswith("http") else _BASE_URL + (href or "")
                jobs.append(
                    self.build_job(
                        title=title.strip(),
                        company=company.strip(),
                        url=job_url,
                        source="instahyre",
                        location=loc_text.strip(),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"InstaHyre card parse error: {exc}")

        await page.close()
        return jobs
