"""
agent/scraper/foundit.py — Playwright-based scraper for Foundit.in.

Foundit (formerly Monster India) is a general-purpose job board with a
large Indian market presence. This scraper browses search results
without requiring login.

TODO: Verify CSS selectors against live Foundit.in before first run.
"""

from typing import Any

from loguru import logger

from agent.scraper.base_scraper import BaseScraper

# TODO: Verify selectors on live Foundit.in
_SELECTORS = {
    "job_card": "div.srpResultCardContainer",
    "title": "h3.jobTitle a",
    "company": "span.companyName",
    "location": "span.location",
    "link": "h3.jobTitle a",
    "next_page": "a.pagination-next",
}

_BASE_URL = "https://www.foundit.in"
_SEARCH_URL = "https://www.foundit.in/srp/results?query={role}&location={location}"


class FounditScraper(BaseScraper):
    """Scraper for Foundit.in (formerly Monster India) using Playwright."""

    async def scrape(self, preferences: dict) -> list[dict[str, Any]]:
        """Scrape job listings from Foundit.in.

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
            logger.warning("FounditScraper: playwright not installed — skipping.")
            return jobs

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()

            for role in roles:
                for location in locations:
                    try:
                        page_jobs = await self._scrape_search(context, role, location)
                        jobs.extend(page_jobs)
                    except Exception as exc:  # noqa: BLE001
                        logger.error(f"FounditScraper error for '{role}'/'{location}': {exc}")

            await context.close()
            await browser.close()

        seen: set[str] = set()
        unique = [j for j in jobs if not (j["url"] in seen or seen.add(j["url"]))]
        logger.info(f"FounditScraper: {len(unique)} unique jobs found")
        return unique

    async def _scrape_search(self, context: Any, role: str, location: str) -> list[dict[str, Any]]:
        """Paginate through search results and extract job cards."""
        page = await context.new_page()
        url = _SEARCH_URL.format(role=role.replace(" ", "+"), location=location.replace(" ", "+"))
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        jobs: list[dict[str, Any]] = []

        for _page_num in range(1, 4):  # max 3 pages
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
                    job_url = href if href and href.startswith("http") else _BASE_URL + (href or "")
                    jobs.append(
                        self.build_job(
                            title=title.strip(),
                            company=company.strip(),
                            url=job_url,
                            source="foundit",
                            location=loc_text.strip(),
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"FounditScraper: card parse error: {exc}")

            next_btn = page.locator(_SELECTORS["next_page"])
            if not await next_btn.is_visible():
                break
            await next_btn.click()
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)

        await page.close()
        return jobs
