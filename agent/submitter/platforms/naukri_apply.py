"""
agent/submitter/platforms/naukri_apply.py — Naukri apply flow.

Uses the saved Naukri browser session to click the "Apply" button on
job listings.  Naukri's apply flow is largely profile-based, so most
fields are auto-filled from the stored candidate profile.

TODO: Verify selectors against live Naukri apply pages before first run.
"""

from typing import Any

from loguru import logger

from agent.submitter.session_manager import SessionManager


class NaukriApplyHandler:
    """Handle application submission on Naukri.com."""

    # TODO: Verify these selectors on live Naukri apply pages
    _SELECTORS = {
        "apply_btn": "button[data-ga-track='Apply']",
        "cover_letter_area": "textarea#coverLetter",
        "submit_btn": "button#apply-button",
        "success_indicator": "div.success-message",
    }

    async def fill(
        self,
        page: Any,
        job: dict[str, Any],
        profile: dict[str, Any],
        cover_letter: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Click Apply on a Naukri job listing and fill the cover letter.

        Args:
            page: A Playwright ``Page`` instance (fresh, no session).
            job: Canonical job dict.
            profile: Profile dict loaded from profile.yaml.
            cover_letter: Generated cover letter string.
            dry_run: If True, navigate but do not submit.

        Returns:
            Result dict with ``status`` and ``error`` keys.
        """
        from playwright.async_api import async_playwright  # type: ignore[import]

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            session_mgr = SessionManager()
            context = await session_mgr.load_session("naukri", browser)
            session_page = await context.new_page()

            try:
                await session_page.goto(job["url"], wait_until="domcontentloaded", timeout=30_000)

                apply_btn = session_page.locator(self._SELECTORS["apply_btn"])
                if await apply_btn.count() > 0:
                    await apply_btn.first.click()
                    await session_page.wait_for_load_state("domcontentloaded", timeout=10_000)

                cover_area = session_page.locator(self._SELECTORS["cover_letter_area"])
                if await cover_area.count() > 0:
                    await cover_area.fill(cover_letter[:2000])

                if dry_run:
                    logger.info("Naukri: dry_run=True — not submitting")
                    return {"status": "dry_run", "error": ""}

                submit_btn = session_page.locator(self._SELECTORS["submit_btn"])
                if await submit_btn.count() > 0:
                    await submit_btn.first.click()
                    await session_page.wait_for_load_state("domcontentloaded", timeout=15_000)

                logger.info(f"Naukri: applied to {job.get('company')} — {job.get('title')}")
                return {"status": "applied", "error": ""}

            except Exception as exc:  # noqa: BLE001
                logger.error(f"NaukriApplyHandler error: {exc}")
                return {"status": "failed", "error": str(exc)}
            finally:
                await context.close()
                await browser.close()
