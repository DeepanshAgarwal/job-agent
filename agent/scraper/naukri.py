"""
agent/scraper/naukri.py — Naukri.com scraper using real Chrome browser.

Naukri is behind Akamai CDN which blocks headless Chromium. Their JSON
API also requires a reCAPTCHA token. The fix is to use the real Chrome
binary (channel="chrome") with headless=False positioned off-screen —
Akamai's bot detection passes on a real Chrome binary with
AutomationControlled disabled.

Session cookies are loaded from auth/naukri_session.json so the scraper
behaves as a logged-in user (avoids login redirects and captcha walls).
"""

import re
from typing import Any

from loguru import logger

from agent.scraper.base_scraper import BaseScraper
from agent.submitter.session_manager import SessionManager

_BASE_URL = "https://www.naukri.com"
_CARD_SELECTORS = [
    "div.cust-job-tuple",
    "article.jobTuple",
    "div[class*='jobTuple']",
    "div[class*='job-tuple']",
    "div[class*='srp-jobtuple']",
    "div[class*='job-card']",
    "li[class*='jobTuple']",
    "div[data-job-id]",
    "article[data-job-id]",
]
_JOB_LINK_RE = re.compile(r"/job-listings-")

# Chrome launch args that reduce bot-detection signals
_CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--window-position=-32000,-32000",   # off-screen; user won't see the window
    "--no-sandbox",
    "--disable-dev-shm-usage",
]


class NaukriScraper(BaseScraper):
    """Scraper for Naukri.com using real Chrome + saved session."""

    async def scrape(self, preferences: dict, on_search_done: Any = None) -> list[dict[str, Any]]:
        roles: list[str] = preferences.get("roles", ["Software Engineer"])
        locations: list[str] = preferences.get("locations", ["Bangalore"])
        jobs: list[dict[str, Any]] = []

        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ImportError:
            logger.warning("NaukriScraper: playwright not installed — skipping.")
            return jobs

        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(
                    channel="chrome",
                    headless=False,
                    args=_CHROME_ARGS,
                    ignore_default_args=["--enable-automation"],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"NaukriScraper: real Chrome unavailable ({exc}) — falling back to Chromium")
                browser = await pw.chromium.launch(headless=True)

            session_mgr = SessionManager()
            try:
                context = await session_mgr.load_session("naukri", browser)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"NaukriScraper: session load failed ({exc}) — using fresh context")
                context = await browser.new_context()

            total = len(roles) * len(locations)
            done = 0
            for role in roles:
                for location in locations:
                    done += 1
                    logger.debug(f"NaukriScraper [{done}/{total}]: '{role}' / '{location}'")
                    try:
                        page_jobs = await self._scrape_search(context, role, location)
                        jobs.extend(page_jobs)
                    except Exception as exc:  # noqa: BLE001
                        logger.error(f"NaukriScraper error for '{role}'/'{location}': {exc}")
                    if on_search_done:
                        on_search_done()

            await context.close()
            await browser.close()

        seen: set[str] = set()
        unique = [j for j in jobs if j["url"] not in seen and not seen.add(j["url"])]
        logger.info(f"NaukriScraper: {len(unique)} unique jobs found")
        return unique

    async def _scrape_search(
        self, context: Any, role: str, location: str
    ) -> list[dict[str, Any]]:
        is_remote = location.lower() in ("remote", "work from home", "wfh")
        role_slug = role.lower().replace(" ", "-")
        loc_slug = location.lower().replace(" ", "-")

        if is_remote:
            url = f"{_BASE_URL}/{role_slug}-jobs?workfromhome=1"
        else:
            url = f"{_BASE_URL}/{role_slug}-jobs-in-{loc_slug}"

        page = await context.new_page()
        jobs: list[dict[str, Any]] = []

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=40_000)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"NaukriScraper: goto failed for '{role}'/'{location}': {exc}")
            await page.close()
            return jobs

        current_url = page.url.lower()
        if any(s in current_url for s in ("login", "signin", "captcha", "verify")):
            logger.warning(
                f"NaukriScraper: redirected to login/captcha for "
                f"'{role}'/'{location}' — session may be expired."
            )
            await page.close()
            return jobs

        # Wait for React hydration — poll for cards rather than a fixed sleep
        try:
            await page.wait_for_selector(
                ", ".join(_CARD_SELECTORS),
                state="attached",
                timeout=8_000,
            )
        except Exception:  # noqa: BLE001
            pass  # timeout is fine — we'll check what's rendered

        # --- Primary: card selectors ---
        cards = []
        matched_sel = ""
        for sel in _CARD_SELECTORS:
            cards = await page.query_selector_all(sel)
            if cards:
                matched_sel = sel
                break

        if cards:
            logger.debug(f"NaukriScraper: {len(cards)} cards via '{matched_sel}' for '{role}'/'{location}'")
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
                    company = (
                        (await company_el.inner_text()).strip()
                        if company_el
                        else "Unknown"
                    )

                    loc_el = await card.query_selector(
                        "li.fleft, span.locWdth, .loc, [class*='location']"
                    )
                    location_text = (
                        (await loc_el.inner_text()).strip() if loc_el else location
                    )

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

        # --- Fallback: harvest job links ---
        if not jobs:
            links = await page.query_selector_all("a[href*='/job-listings-']")
            logger.debug(f"NaukriScraper: fallback link harvest → {len(links)} links for '{role}'/'{location}'")
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

        if not jobs:
            import re as _re  # noqa: PLC0415
            safe_role = _re.sub(r"[^a-z0-9]", "_", role.lower())[:20]
            safe_loc = _re.sub(r"[^a-z0-9]", "_", location.lower())[:10]
            shot_path = f"data/screenshots/naukri_debug_{safe_role}_{safe_loc}.png"
            try:
                await page.screenshot(path=shot_path)
                logger.warning(f"NaukriScraper: 0 jobs for '{role}'/'{location}' — screenshot → {shot_path}")
            except Exception:  # noqa: BLE001
                pass

        await page.close()
        return jobs

