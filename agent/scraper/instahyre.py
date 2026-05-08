"""
agent/scraper/instahyre.py — Playwright-based scraper for Instahyre.com.

Instahyre is a tech-focused platform well-suited for senior engineering
roles. This scraper logs in via a saved session, searches by role and
location, and extracts job cards.

Job links follow the pattern /job-{id}-{slug}-at-{company}-{location}/
"""

import re
from typing import Any

from loguru import logger

from agent.scraper.base_scraper import BaseScraper
from agent.submitter.session_manager import SessionManager

_BASE_URL = "https://www.instahyre.com"
_SEARCH_URL = "https://www.instahyre.com/search-jobs/?q={role}&l={location}"
# Instahyre job URLs: /job-{numeric_id}-{slug}/
_JOB_LINK_RE = re.compile(r"^/job-\d+-")


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
            logger.warning("InstaHyreScraper: playwright not installed — skipping.")
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
        """Navigate search results and extract job listings.

        Tries known card selectors first, falls back to harvesting
        all <a href='/job-{id}-...'> links from the rendered page.
        """
        page = await context.new_page()
        loc_param = "" if location.lower() == "remote" else location.replace(" ", "+")
        url = _SEARCH_URL.format(role=role.replace(" ", "+"), location=loc_param)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"InstaHyreScraper: goto failed for '{role}'/'{location}': {exc}")
            await page.close()
            return []

        jobs: list[dict[str, Any]] = []

        # --- Primary: try known card selectors ---
        _CARD_SELECTORS = [
            "div.opening-item",
            "div.job-opening",
            "div[class*='opening']",
            "div[class*='job-card']",
        ]
        cards = []
        for sel in _CARD_SELECTORS:
            cards = await page.query_selector_all(sel)
            if cards:
                break

        if cards:
            for card in cards:
                try:
                    link_el = await card.query_selector("a[href^='/job-']")
                    if link_el is None:
                        continue
                    href = await link_el.get_attribute("href") or ""
                    if not _JOB_LINK_RE.match(href):
                        continue
                    job_url = _BASE_URL + href

                    title_el = await card.query_selector("h2, h3, .job-title, [class*='title']")
                    title = (await title_el.inner_text()).strip() if title_el else (await link_el.inner_text()).strip()

                    company_el = await card.query_selector(".company-name, [class*='company'], strong")
                    company = (await company_el.inner_text()).strip() if company_el else "Unknown"

                    loc_el = await card.query_selector(".location, [class*='location'], .city")
                    loc_text = (await loc_el.inner_text()).strip() if loc_el else location

                    jobs.append(
                        self.build_job(
                            title=title,
                            company=company,
                            url=job_url,
                            source="instahyre",
                            location=loc_text,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"InstaHyreScraper: card parse error: {exc}")

        # --- Fallback: harvest all job links from the rendered page ---
        if not jobs:
            links = await page.query_selector_all("a[href]")
            for link in links:
                try:
                    href = await link.get_attribute("href") or ""
                    if not _JOB_LINK_RE.match(href):
                        continue
                    job_url = _BASE_URL + href

                    # Link text format: "Company - Title  Job available in Location [...]"
                    raw = (await link.inner_text()).strip()
                    if not raw:
                        continue
                    # Strip trailing "View »"
                    raw = re.sub(r"\s*View\s*».*", "", raw, flags=re.DOTALL).strip()
                    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]

                    # First line typically: "Company - Title"
                    first = lines[0] if lines else ""
                    if " - " in first:
                        company, title = first.split(" - ", 1)
                    else:
                        title, company = first, "Unknown"

                    loc_text = location
                    for ln in lines:
                        m = re.search(r"Job available in (.+)", ln)
                        if m:
                            loc_text = m.group(1).strip()
                            break

                    jobs.append(
                        self.build_job(
                            title=title.strip(),
                            company=company.strip(),
                            url=job_url,
                            source="instahyre",
                            location=loc_text,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"InstaHyreScraper: link parse error: {exc}")

        await page.close()
        return jobs
