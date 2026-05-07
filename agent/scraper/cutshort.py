"""
agent/scraper/cutshort.py — Playwright/API scraper for Cutshort.io.

Cutshort focuses on startup roles. This scraper uses Playwright to
browse the public job feed (no auth required for basic listings).

TODO: Verify CSS selectors against live Cutshort.io before first run.
TODO: Cutshort may expose a public GraphQL/REST API; check for API-first
      approach to get cleaner data without DOM parsing.
"""

from typing import Any

from loguru import logger

from agent.scraper.base_scraper import BaseScraper

# TODO: Verify selectors on live Cutshort.io
_SELECTORS = {
    "job_card": "div[data-cy='job-listing-card']",
    "title": "h2[data-cy='job-title']",
    "company": "span[data-cy='company-name']",
    "location": "span[data-cy='location']",
    "link": "a[data-cy='job-card-link']",
}

_SEARCH_URL = "https://cutshort.io/jobs?query={role}"
_BASE_URL = "https://cutshort.io"


class CutshortScraper(BaseScraper):
    """Scraper for Cutshort.io using Playwright."""

    async def scrape(self, preferences: dict) -> list[dict[str, Any]]:
        """Scrape job listings from Cutshort.io.

        Args:
            preferences: Loaded preferences.yaml dict.

        Returns:
            List of canonical job dicts; empty list on any failure.
        """
        roles: list[str] = preferences.get("roles", ["Software Engineer"])
        jobs: list[dict[str, Any]] = []

        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ImportError:
            logger.warning("playwright not installed — skipping Cutshort scraping.")
            return jobs

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()

            for role in roles:
                try:
                    page_jobs = await self._scrape_role(context, role)
                    jobs.extend(page_jobs)
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"CutshortScraper error for '{role}': {exc}")

            await context.close()
            await browser.close()

        seen: set[str] = set()
        unique = [j for j in jobs if not (j["url"] in seen or seen.add(j["url"]))]
        logger.info(f"CutshortScraper: {len(unique)} unique jobs found")
        return unique

    async def _scrape_role(self, context: Any, role: str) -> list[dict[str, Any]]:
        """Scrape job listings for a single role."""
        page = await context.new_page()
        url = _SEARCH_URL.format(role=role.replace(" ", "+"))
        await page.goto(url, wait_until="networkidle", timeout=30_000)

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
                        source="cutshort",
                        location=loc_text.strip(),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"Cutshort card parse error: {exc}")

        await page.close()
        return jobs
