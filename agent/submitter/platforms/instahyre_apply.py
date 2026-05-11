"""
agent/submitter/platforms/instahyre_apply.py — Instahyre apply flow.

Uses the saved Instahyre session to submit applications.  Instahyre
typically shows an "Express Interest" or "Apply" button that triggers
the site's own application flow using the candidate's stored profile.

TODO: Verify selectors against live Instahyre apply pages before first run.
"""

from typing import Any

from loguru import logger

from agent.submitter.session_manager import SessionManager


class InstahyreApplyHandler:
    """Handle application submission on Instahyre.com."""

    # TODO: Verify these selectors on live Instahyre apply pages
    _SELECTORS = {
        "apply_btn": "button.apply-btn",
        "cover_letter_area": "textarea.cover-letter-input",
        "submit_btn": "button.submit-application",
        "success_indicator": "div.application-success",
    }

    async def fill(
        self,
        page: Any,
        job: dict[str, Any],
        profile: dict[str, Any],
        cover_letter: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Submit an application on Instahyre.com.

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
            context = await session_mgr.load_session("instahyre", browser)
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
                    logger.info("Instahyre: dry_run=True — not submitting")
                    return {"status": "dry_run", "error": ""}

                submit_btn = session_page.locator(self._SELECTORS["submit_btn"])
                if await submit_btn.count() == 0:
                    return {"status": "failed", "error": "Submit button not found"}
                await submit_btn.first.click()
                await session_page.wait_for_load_state("domcontentloaded", timeout=15_000)

                success = session_page.locator(self._SELECTORS["success_indicator"])
                if await success.count() == 0:
                    logger.warning(f"Instahyre: success indicator not found for {job.get('company')} — may have failed")

                logger.info(f"Instahyre: applied to {job.get('company')} — {job.get('title')}")
                return {"status": "applied", "error": ""}

            except Exception as exc:  # noqa: BLE001
                logger.error(f"InstahyreApplyHandler error: {exc}")
                return {"status": "failed", "error": str(exc)}
            finally:
                try:
                    screenshot_path = f"data/screenshots/instahyre_{job.get('company', 'unknown')[:20]}.png"
                    await session_page.screenshot(path=screenshot_path)
                except Exception:  # noqa: BLE001
                    pass
                await context.close()
                await browser.close()
