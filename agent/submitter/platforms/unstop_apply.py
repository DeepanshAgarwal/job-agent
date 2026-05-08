"""
agent/submitter/platforms/unstop_apply.py — Unstop apply flow.

Uses the saved Unstop session to submit job applications. Unstop is
a React SPA; this handler waits for the apply button to become interactive
before clicking and filling any required fields.

TODO: Verify selectors against live Unstop apply pages before first run.
"""

from typing import Any

from loguru import logger

from agent.submitter.session_manager import SessionManager


class UnstopApplyHandler:
    """Handle application submission on Unstop.com."""

    # TODO: Verify these selectors on live Unstop apply pages
    _SELECTORS = {
        "apply_btn": "button.apply-now-btn",
        "cover_letter_area": "textarea.cover-letter",
        "submit_btn": "button[type='submit'].apply-submit",
        "success_indicator": "div.application-submitted",
    }

    async def fill(
        self,
        page: Any,
        job: dict[str, Any],
        profile: dict[str, Any],
        cover_letter: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Submit an application on Unstop.com.

        Args:
            page: A Playwright ``Page`` instance (pre-loaded with saved session).
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
            context = await session_mgr.load_session("unstop", browser)
            session_page = await context.new_page()

            try:
                await session_page.goto(job["url"], wait_until="networkidle", timeout=45_000)

                # Wait for apply button (Unstop is SPA — content loads after initial render)
                apply_btn = session_page.locator(self._SELECTORS["apply_btn"])
                try:
                    await apply_btn.wait_for(timeout=10_000)
                except Exception:  # noqa: BLE001
                    logger.warning(f"UnstopApply: apply button not found for {job.get('url')}")
                    return {"status": "failed", "error": "Apply button not found"}

                await apply_btn.click()
                await session_page.wait_for_timeout(1500)

                # Fill cover letter if field is visible
                cl_area = session_page.locator(self._SELECTORS["cover_letter_area"])
                if await cl_area.count() > 0:
                    await cl_area.fill(cover_letter)

                if dry_run:
                    logger.info("UnstopApply: dry_run=True — form interacted but not submitted")
                    return {"status": "dry_run", "error": ""}

                submit_btn = session_page.locator(self._SELECTORS["submit_btn"])
                if await submit_btn.count() > 0:
                    await submit_btn.click()
                    await session_page.wait_for_load_state("networkidle", timeout=20_000)

                logger.info(f"UnstopApply: applied to {job.get('company')} — {job.get('title')}")
                return {"status": "applied", "error": ""}

            except Exception as exc:  # noqa: BLE001
                logger.error(f"UnstopApplyHandler.fill error: {exc}")
                return {"status": "failed", "error": str(exc)}
            finally:
                await session_page.close()
                await context.close()
                await browser.close()
