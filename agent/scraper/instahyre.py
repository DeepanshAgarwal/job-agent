"""
agent/scraper/instahyre.py — Playwright-based scraper for Instahyre.com.

Instahyre is behind Cloudflare bot protection. We bypass this by launching
the real Chrome binary (channel="chrome") with a persistent user-data-dir
that carries genuine Cloudflare clearance cookies from the user's real
browsing session.

Job links follow the pattern /job-{id}-{slug}-at-{company}-{location}/
"""

import re
from pathlib import Path
from typing import Any

from loguru import logger

from agent.scraper.base_scraper import BaseScraper

_BASE_URL = "https://www.instahyre.com"
_SEARCH_URL = "https://www.instahyre.com/search-jobs/?q={role}&l={location}"
# Instahyre job URLs: /job-{id}-{slug}/ OR full https://www.instahyre.com/job-{id}-...
_JOB_LINK_RE = re.compile(r"(?:https://www\.instahyre\.com)?/job-\d+-")
# Persistent Chrome profile path — carries Cloudflare clearance cookies
_CDP_PROFILE = Path(__file__).parent.parent.parent / "auth" / "chrome-cdp-profile"


class InstaHyreScraper(BaseScraper):
    """Scraper for Instahyre.com using real Chrome + saved Cloudflare session."""

    async def scrape(self, preferences: dict, on_search_done: Any = None) -> list[dict[str, Any]]:
        roles: list[str] = preferences.get("roles", ["Software Engineer"])
        locations: list[str] = preferences.get("locations", ["Bangalore"])
        jobs: list[dict[str, Any]] = []

        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ImportError:
            logger.warning("InstaHyreScraper: playwright not installed — skipping.")
            return jobs

        async with async_playwright() as pw:
            # Use real Chrome binary + persistent CDP profile to bypass Cloudflare.
            # headless=False is required — Cloudflare Turnstile detects headless Chrome.
            # On the FIRST run: a browser window will appear; solve the security check
            # once. The cf_clearance cookie is then saved to the CDP profile and future
            # runs will skip the challenge automatically.
            try:
                context = await pw.chromium.launch_persistent_context(
                    user_data_dir=str(_CDP_PROFILE),
                    channel="chrome",
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                    ignore_default_args=["--enable-automation"],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"InstaHyreScraper: Chrome launch failed ({exc}) — falling back to Chromium (may be blocked by Cloudflare)")
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context()

            total = len(roles) * len(locations)
            done = 0
            for role in roles:
                for location in locations:
                    done += 1
                    logger.debug(f"InstaHyreScraper [{done}/{total}]: '{role}' / '{location}'")
                    try:
                        page_jobs = await self._scrape_search(context, role, location)
                        jobs.extend(page_jobs)
                    except Exception as exc:  # noqa: BLE001
                        logger.error(f"InstaHyreScraper error for '{role}'/'{location}': {exc}")
                    if on_search_done:
                        on_search_done()

            await context.close()

        seen: set[str] = set()
        unique = [j for j in jobs if not (j["url"] in seen or seen.add(j["url"]))]
        logger.info(f"InstaHyreScraper: {len(unique)} unique jobs found")
        return unique

    async def _scrape_search(
        self, context: Any, role: str, location: str
    ) -> list[dict[str, Any]]:
        loc_param = "" if location.lower() == "remote" else location.replace(" ", "+")
        # Instahyre is a single-page SPA — all results load on one URL.
        url = _SEARCH_URL.format(role=role.replace(" ", "+"), location=loc_param)

        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"InstaHyreScraper: goto failed for '{role}'/'{location}': {exc}")
            await page.close()
            return []

        # Cloudflare challenge — wait up to 30s for the user to solve it in the
        # visible browser window. cf_clearance is stored in the CDP profile so
        # subsequent runs skip this entirely.
        for _ in range(6):
            body_snippet = ""
            try:
                body_snippet = (await page.inner_text("body"))[:200]
            except Exception:  # noqa: BLE001
                pass
            if "Performing security verification" in body_snippet or "checking your browser" in body_snippet.lower():
                logger.warning(
                    "InstaHyreScraper: Cloudflare challenge detected — "
                    "please solve it in the browser window (waiting up to 30s)…"
                )
                await page.wait_for_timeout(5_000)
            else:
                break

        # Detect login redirect
        current_url = page.url.lower()
        if any(s in current_url for s in ("login", "signin", "sign-in", "auth")):
            logger.warning(f"InstaHyreScraper: redirected to login for '{role}'/'{location}' — session expired.")
            await page.close()
            return []

        # Wait for React to inject job cards. Nav links appear immediately so we
        # scope the selector to result containers only.
        try:
            await page.wait_for_selector(
                "main a[href*='/job-'], [class*='opening'] a, [class*='job-list'] a[href*='/job-']",
                state="attached",
                timeout=8_000,
            )
        except Exception:  # noqa: BLE001
            pass  # timeout is fine — we'll check what's rendered

        jobs: list[dict[str, Any]] = []

        # --- Primary: card selectors ---
        _CARD_SELECTORS = [
            "div.opening-item",
            "div.job-opening",
            "div[class*='opening-item']",
            "div[class*='opening']",
            "div[class*='job-card']",
            "div[class*='JobCard']",
            "li[class*='opening']",
            "div[class*='job-list'] > div",
            "article[class*='job']",
        ]
        cards = []
        for sel in _CARD_SELECTORS:
            cards = await page.query_selector_all(sel)
            if cards:
                break

        if cards:
            logger.debug(f"InstaHyreScraper: {len(cards)} cards via '{sel}' for '{role}'/'{location}'")
            for card in cards:
                try:
                    link_el = await card.query_selector("a[href^='/job-']")
                    if link_el is None:
                        link_el = await card.query_selector("a")
                    if link_el is None:
                        continue
                    href = await link_el.get_attribute("href") or ""
                    if not href or not _JOB_LINK_RE.search(href):
                        continue
                    job_url = href if href.startswith("http") else _BASE_URL + href

                    title_el = await card.query_selector("h2, h3, .job-title, [class*='title']")
                    title = (await title_el.inner_text()).strip() if title_el else ""
                    if not title:
                        raw = (await link_el.inner_text()).strip()
                        title = raw or "Unknown"

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

        # --- Fallback: harvest job links directly ---
        if not jobs:
            all_links = await page.query_selector_all("a[href]")
            for link in all_links:
                try:
                    href = await link.get_attribute("href") or ""
                    if not _JOB_LINK_RE.search(href):
                        continue
                    job_url = href if href.startswith("http") else _BASE_URL + href

                    # Link text: "Company - Title  Job available in Location [...]"
                    raw = (await link.inner_text()).strip()
                    if not raw:
                        continue
                    raw = re.sub(r"\s*View\s*».*", "", raw, flags=re.DOTALL).strip()
                    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]

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

        if not jobs:
            import re as _re  # noqa: PLC0415
            safe_role = _re.sub(r"[^a-z0-9]", "_", role.lower())[:20]
            safe_loc = _re.sub(r"[^a-z0-9]", "_", location.lower())[:10]
            shot_path = f"data/screenshots/instahyre_debug_{safe_role}_{safe_loc}.png"
            try:
                await page.screenshot(path=shot_path)
                logger.warning(f"InstaHyreScraper: 0 jobs for '{role}'/'{location}' — screenshot → {shot_path}")
            except Exception:  # noqa: BLE001
                pass

        await page.close()
        return jobs
