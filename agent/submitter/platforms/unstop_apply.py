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

    # Selectors tried individually in order
    _APPLY_BTN = [
        "button:has-text('Quick Apply')",
        "button:has-text('Apply now')",
        "button:has-text('Apply Now')",
        "a:has-text('Quick Apply')",
        "div.apply-btn-wrap button",
        "aside button",
        "button.apply-now-btn",
        "button[class*='apply']",
    ]
    _CL_AREA = [
        "textarea.cover-letter",
        "textarea[name*='cover']",
        "textarea",
    ]
    _SUBMIT_BTN = [
        "button[type='submit'].apply-submit",
        "button[type='submit']:has-text('Submit')",
        "button:has-text('Submit')",
        "button:has-text('Apply')",
    ]
    _SUCCESS = [
        "div.application-submitted",
        "div:has-text('applied successfully')",
        "div:has-text('Application submitted')",
        "div:has-text('Successfully applied')",
    ]

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
                original_url = job["url"]
                await session_page.goto(original_url, wait_until="networkidle", timeout=45_000)

                # Explicitly wait for the Quick Apply button — Unstop is a React
                # SPA and the apply button renders several seconds after paint.
                _BTN_SEL = ", ".join(self._APPLY_BTN[:4])
                try:
                    await session_page.wait_for_selector(_BTN_SEL, timeout=12_000, state="visible")
                except Exception:  # noqa: BLE001
                    pass
                await session_page.wait_for_timeout(1_000)

                # ── Click apply button ────────────────────────────────────────
                clicked = False
                for sel in self._APPLY_BTN:
                    try:
                        btn = session_page.locator(sel).first
                        if await btn.is_visible(timeout=2_000):
                            await btn.click()
                            clicked = True
                            logger.debug(f"UnstopApply: clicked apply via '{sel}'")
                            break
                    except Exception:  # noqa: BLE001
                        pass

                if not clicked:
                    logger.warning(f"UnstopApply: apply button not found for {original_url}")
                    return {"status": "failed", "error": "Apply button not found"}

                await session_page.wait_for_timeout(2_000)

                # ── Fill cover letter if visible ──────────────────────────────
                for cl_sel in self._CL_AREA:
                    try:
                        cl = session_page.locator(cl_sel).first
                        if await cl.is_visible(timeout=800):
                            await cl.fill(cover_letter)
                            break
                    except Exception:  # noqa: BLE001
                        pass

                if dry_run:
                    logger.info("UnstopApply: dry_run=True — form interacted but not submitted")
                    return {"status": "dry_run", "error": ""}

                # ── Submit ────────────────────────────────────────────────────
                submitted = False
                for sub_sel in self._SUBMIT_BTN:
                    try:
                        sub = session_page.locator(sub_sel).first
                        if await sub.is_visible(timeout=2_000):
                            await sub.click()
                            submitted = True
                            logger.debug(f"UnstopApply: clicked submit via '{sub_sel}'")
                            break
                    except Exception:  # noqa: BLE001
                        pass

                if not submitted:
                    return {"status": "failed", "error": "Submit button not found"}

                await session_page.wait_for_timeout(4_000)

                # ── Check success ─────────────────────────────────────────────
                confirmed = False
                for succ_sel in self._SUCCESS:
                    try:
                        if await session_page.locator(succ_sel).count() > 0:
                            confirmed = True
                            break
                    except Exception:  # noqa: BLE001
                        pass

                # URL changed away from job page = success
                if not confirmed and session_page.url != original_url:
                    confirmed = True
                    logger.debug("UnstopApply: URL changed after submit — treating as success")

                if not confirmed:
                    logger.warning(f"UnstopApply: no success indicator for {job.get('company')} — marking failed")
                    return {"status": "failed", "error": "no confirmation after submit"}

                logger.info(f"UnstopApply: applied to {job.get('company')} — {job.get('title')}")
                return {"status": "applied", "error": ""}

            except Exception as exc:  # noqa: BLE001
                logger.error(f"UnstopApplyHandler.fill error: {exc}")
                return {"status": "failed", "error": str(exc)}
            finally:
                try:
                    screenshot_path = f"data/screenshots/unstop_{job.get('company', 'unknown')[:20]}.png"
                    await session_page.screenshot(path=screenshot_path)
                except Exception:  # noqa: BLE001
                    pass
                await session_page.close()
                await context.close()
                await browser.close()
