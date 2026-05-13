"""
agent/scraper/unstop.py — Playwright-based scraper for Unstop.com.

Unstop (formerly Dare2Compete) lists competitions, hackathons, and
fresher/campus jobs — highly relevant for recent graduates. This scraper
logs in via a saved session and extracts job listings.
"""

import re
from typing import Any

from loguru import logger

from agent.scraper.base_scraper import BaseScraper
from agent.submitter.session_manager import SessionManager

_BASE_URL = "https://unstop.com"
# Correct query param is searchTerm (not search)
_SEARCH_URL = "https://unstop.com/jobs?searchTerm={role}"

# Pattern to identify real job detail links (slug-numeric_id)
_JOB_LINK_RE = re.compile(r"/jobs/[^/?#]+-\d+$")


class UnstopScraper(BaseScraper):
    """Scraper for Unstop.com using Playwright + saved session."""

    async def scrape(self, preferences: dict, on_search_done: Any = None) -> list[dict[str, Any]]:
        """Scrape job listings from Unstop.com.

        Args:
            preferences: Loaded preferences.yaml dict.
            on_search_done: Optional callback invoked after each role search completes.

        Returns:
            List of canonical job dicts; empty list on any failure.
        """
        roles: list[str] = preferences.get("roles", ["Software Engineer"])

        jobs: list[dict[str, Any]] = []

        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ImportError:
            logger.warning("UnstopScraper: playwright not installed — skipping.")
            return jobs

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            session_mgr = SessionManager()
            context = await session_mgr.load_session("unstop", browser)

            for role in roles:
                try:
                    page_jobs = await self._scrape_search(context, role)
                    jobs.extend(page_jobs)
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"UnstopScraper error for '{role}': {exc}")
                finally:
                    if on_search_done:
                        on_search_done()

            await context.close()
            await browser.close()

        seen: set[str] = set()
        unique = [j for j in jobs if not (j["url"] in seen or seen.add(j["url"]))]
        logger.info(f"UnstopScraper: {len(unique)} unique jobs found")
        return unique

    async def _scrape_search(self, context: Any, role: str) -> list[dict[str, Any]]:
        """Navigate search results and extract job listings.

        Tries known Angular component selectors first; falls back to
        harvesting <a> links that match the /jobs/{slug}-{id} pattern.
        """
        page = await context.new_page()
        url = _SEARCH_URL.format(role=role.replace(" ", "+"))

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            # Let Angular finish rendering (SPA needs time after domcontentloaded)
            await page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:  # noqa: BLE001
            pass  # networkidle timeout is non-fatal; proceed with whatever rendered

        jobs: list[dict[str, Any]] = []

        # --- Primary: try known Angular card component selectors ---
        _CARD_SELECTORS = [
            "app-individual-opportunity",
            "div.single-opportunity",
            "div.opportunity-wrapper",
            "div[class*='card'][class*='opportunity']",
        ]
        cards = []
        for sel in _CARD_SELECTORS:
            cards = await page.query_selector_all(sel)
            if cards:
                break

        if cards:
            for card in cards:
                try:
                    title_el = await card.query_selector("h2, h3, .opp-title, .title, [class*='title']")
                    link_el = await card.query_selector("a[href*='/jobs/']")
                    if title_el is None or link_el is None:
                        continue

                    title = (await title_el.inner_text()).strip()
                    href = await link_el.get_attribute("href") or ""
                    if not _JOB_LINK_RE.search(href):
                        continue

                    company_el = await card.query_selector(".company-name, .org-name, [class*='company']")
                    company = (await company_el.inner_text()).strip() if company_el else "Unknown"

                    loc_el = await card.query_selector(".location, [class*='location']")
                    location = (await loc_el.inner_text()).strip() if loc_el else "India"

                    # Extract skill tags / eligible branches shown on the listing card
                    # so the embedding scorer gets meaningful text instead of empty string.
                    skill_els = (
                        await card.query_selector_all(".opp-tag, .tags-bar span, [class*='tag'] span")
                        or await card.query_selector_all("[class*='skill'], [class*='branch'], [class*='eligible']")
                    )
                    skills: list[str] = []
                    for el in skill_els[:12]:
                        txt = (await el.inner_text()).strip()
                        if txt:
                            skills.append(txt)
                    description = (
                        f"{title} at {company}, {location}. Skills: {', '.join(skills)}"
                        if skills
                        else f"{title} at {company}, {location}"
                    )

                    job_url = href if href.startswith("http") else _BASE_URL + href
                    jobs.append(
                        self.build_job(
                            title=title,
                            company=company,
                            url=job_url,
                            source="unstop",
                            location=location,
                            description=description,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"UnstopScraper: card parse error: {exc}")

        # --- Fallback: harvest job links directly from the page ---
        if not jobs:
            links = await page.query_selector_all("a[href]")
            for link in links:
                try:
                    href = await link.get_attribute("href") or ""
                    if not _JOB_LINK_RE.search(href):
                        continue
                    job_url = href if href.startswith("http") else _BASE_URL + href

                    text = (await link.inner_text()).strip()
                    if not text:
                        continue

                    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                    title = lines[0] if lines else "Unknown"
                    company = lines[1] if len(lines) > 1 else "Unknown"

                    location = "India"
                    for ln in lines:
                        if "|" in ln:
                            location = ln.split("|")[-1].strip()
                            break

                    description = f"{title} at {company}, {location}"

                    jobs.append(
                        self.build_job(
                            title=title,
                            company=company,
                            url=job_url,
                            source="unstop",
                            location=location,
                            description=description,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"UnstopScraper: link parse error: {exc}")

        if not jobs:
            logger.debug(f"UnstopScraper: no job cards found for '{role}'")

        await page.close()
        return jobs
