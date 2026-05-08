"""
agent/scraper/hirist.py — Playwright-based scraper for Hirist.com.

Hirist is a tech-only job platform focused on software roles.
This scraper navigates search results and extracts job cards without
requiring login.

TODO: Verify CSS selectors against live Hirist.com before first run.
"""

from typing import Any

from loguru import logger

from agent.scraper.base_scraper import BaseScraper

# TODO: Verify selectors on live Hirist.com
_SELECTORS = {
    "job_card": "div.job-listing",
    "title": "h2.job-title a",
    "company": "span.company",
    "location": "span.location",
    "description": "div.job-summary",
}

_SEARCH_URL = "https://www.hirist.tech/search/{role}/"


class HiristScraper(BaseScraper):
    """Scraper for Hirist.com using Playwright (no login required)."""

    async def scrape(self, preferences: dict) -> list[dict[str, Any]]:
        """Scrape job listings from Hirist.com.

        Args:
            preferences: Loaded preferences.yaml dict.

        Returns:
            List of canonical job dicts; empty list on any failure.
        """
        roles: list[str] = preferences.get("roles", ["Python Developer"])
        jobs: list[dict[str, Any]] = []

        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ImportError:
            logger.warning("HiristScraper: playwright not installed — skipping.")
            return jobs

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()

            for role in roles:
                try:
                    page_jobs = await self._scrape_role(context, role)
                    jobs.extend(page_jobs)
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"HiristScraper error for '{role}': {exc}")

            await context.close()
            await browser.close()

        seen: set[str] = set()
        unique = [j for j in jobs if not (j["url"] in seen or seen.add(j["url"]))]
        logger.info(f"HiristScraper: {len(unique)} unique jobs found")
        return unique

    async def _scrape_role(self, context: Any, role: str) -> list[dict[str, Any]]:
        """Scrape job listings for a single role keyword."""
        page = await context.new_page()
        url = _SEARCH_URL.format(role=role.lower().replace(" ", "-"))
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        jobs: list[dict[str, Any]] = []
        cards = page.locator(_SELECTORS["job_card"])
        count = await cards.count()

        for i in range(count):
            try:
                card = cards.nth(i)
                title_el = card.locator(_SELECTORS["title"])
                title = await title_el.inner_text()
                href = await title_el.get_attribute("href")
                company = await card.locator(_SELECTORS["company"]).inner_text()
                loc_text = await card.locator(_SELECTORS["location"]).inner_text()
                desc = ""
                try:
                    desc = await card.locator(_SELECTORS["description"]).inner_text()
                except Exception:  # noqa: BLE001
                    pass
                jobs.append(
                    self.build_job(
                        title=title.strip(),
                        company=company.strip(),
                        url=href or "",
                        source="hirist",
                        location=loc_text.strip(),
                        description=desc.strip(),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"HiristScraper: card parse error: {exc}")

        await page.close()
        return jobs
