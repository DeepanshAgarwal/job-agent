"""
agent/submitter/platforms/internshala_apply.py — Internshala apply flow.

Uses the saved Internshala session to submit applications. Internshala
uses a simple "Apply Now" modal that pre-fills details from the candidate's
profile. This handler interacts with that modal.

TODO: Verify selectors against live Internshala apply pages before first run.
"""

from typing import Any

from loguru import logger

from agent.submitter.session_manager import SessionManager


class InternshalaApplyHandler:
    """Handle application submission on Internshala.com."""

    # TODO: Verify these selectors on live Internshala apply pages
    _SELECTORS = {
        "apply_btn": "button#apply_button",
        "cover_letter_area": "textarea#cover_letter",
        "availability_checkbox": "input#availability",
        "submit_btn": "button#submit",
        "success_indicator": "div.success-alert",
    }

    async def fill(
        self,
        page: Any,
        job: dict[str, Any],
        profile: dict[str, Any],
        cover_letter: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Submit an application on Internshala.com.

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
            context = await session_mgr.load_session("internshala", browser)
            session_page = await context.new_page()

            try:
                await session_page.goto(job["url"], wait_until="domcontentloaded", timeout=30_000)

                # Click Apply button to open the modal
                apply_btn = session_page.locator(self._SELECTORS["apply_btn"])
                if await apply_btn.count() > 0:
                    await apply_btn.click()
                    await session_page.wait_for_timeout(1500)

                # Fill cover letter if field is visible
                cl_area = session_page.locator(self._SELECTORS["cover_letter_area"])
                if await cl_area.count() > 0:
                    await cl_area.fill(cover_letter)

                if dry_run:
                    logger.info("InternshalaApply: dry_run=True — form interacted but not submitted")
                    return {"status": "dry_run", "error": ""}

                # Submit
                submit_btn = session_page.locator(self._SELECTORS["submit_btn"])
                if await submit_btn.count() > 0:
                    await submit_btn.click()
                    await session_page.wait_for_load_state("domcontentloaded", timeout=15_000)

                logger.info(f"InternshalaApply: applied to {job.get('company')} — {job.get('title')}")
                return {"status": "applied", "error": ""}

            except Exception as exc:  # noqa: BLE001
                logger.error(f"InternshalaApplyHandler.fill error: {exc}")
                return {"status": "failed", "error": str(exc)}
            finally:
                await session_page.close()
                await context.close()
                await browser.close()
