"""
agent/scraper/internshala.py — Playwright-based scraper for Internshala.com.

Internshala is popular in India for internships and fresher/junior roles,
well-suited for recent graduates. This scraper logs in via a saved session,
searches by role, and extracts job/internship cards.
"""

from typing import Any

from loguru import logger

from agent.scraper.base_scraper import BaseScraper
from agent.submitter.session_manager import SessionManager

_BASE_URL = "https://internshala.com"


class InternshalasScraper(BaseScraper):
    """Scraper for Internshala.com using Playwright + saved session."""

    async def scrape(self, preferences: dict) -> list[dict[str, Any]]:
        """Scrape job listings from Internshala.com.

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
            logger.warning("InternshalasScraper: playwright not installed — skipping.")
            return jobs

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            session_mgr = SessionManager()
            context = await session_mgr.load_session("internshala", browser)

            for role in roles:
                try:
                    page_jobs = await self._scrape_search(context, role)
                    jobs.extend(page_jobs)
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"InternshalasScraper: error for '{role}': {exc}")

            await context.close()
            await browser.close()

        seen: set[str] = set()
        unique = [j for j in jobs if not (j["url"] in seen or seen.add(j["url"]))]
        logger.info(f"InternshalasScraper: {len(unique)} unique jobs found")
        return unique

    async def _scrape_search(self, context: Any, role: str) -> list[dict[str, Any]]:
        """Navigate search results and extract job listings.

        Uses query_selector_all (non-waiting element handles) to avoid 30s
        Playwright locator timeouts when selectors don't match.
        """
        page = await context.new_page()
        slug = role.lower().replace(" ", "-")
        url = f"{_BASE_URL}/jobs/{slug}-jobs/"

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            # Short wait for cards; if none appear, bail early
            await page.wait_for_selector(".individual_internship", timeout=8_000)
        except Exception:  # noqa: BLE001
            await page.close()
            return []

        jobs: list[dict[str, Any]] = []
        # query_selector_all returns element handles — no auto-wait, no 30s timeout
        cards = await page.query_selector_all(".individual_internship")

        for card in cards:
            try:
                # Try multiple known title selectors in order of specificity
                title_el = (
                    await card.query_selector("h3 a")
                    or await card.query_selector(".profile a")
                    or await card.query_selector("a.view-detail")
                    or await card.query_selector(".heading_4_5 a")
                    or await card.query_selector("a[href*='/job/detail/']")
                )
                if title_el is None:
                    continue

                title = (await title_el.inner_text()).strip()
                href = await title_el.get_attribute("href")
                job_url = href if href and href.startswith("http") else _BASE_URL + (href or "")

                company_el = (
                    await card.query_selector(".company-name")
                    or await card.query_selector(".link-container")
                )
                company = (await company_el.inner_text()).strip() if company_el else "Unknown"

                loc_el = await card.query_selector(
                    ".locations span, .location_link a, .location-container span"
                )
                location = (await loc_el.inner_text()).strip() if loc_el else "India"

                jobs.append(
                    self.build_job(
                        title=title,
                        company=company,
                        url=job_url,
                        source="internshala",
                        location=location,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"InternshalasScraper: card parse error: {exc}")

        await page.close()
        return jobs
