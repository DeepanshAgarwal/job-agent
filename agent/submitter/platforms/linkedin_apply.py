"""
agent/submitter/platforms/linkedin_apply.py — LinkedIn Easy Apply flow.

LinkedIn has two apply modes:
  1. "Easy Apply" — inline modal form (session required, handled here)
  2. "Apply"       — redirects to company's external ATS

This handler loads the saved LinkedIn session and attempts Easy Apply.
LinkedIn's Easy Apply form is multi-step and dynamically rendered.
We click through up to 5 steps, filling the cover letter if a field
appears, then submit.

Session file: auth/linkedin_session.json
Capture with setup.py → "Login to LinkedIn" step.

Note on LinkedIn anti-scraping:
  LinkedIn actively detects headless Chromium.  The session cookie approach
  works for applying (since we already have auth cookies), but if LinkedIn
  challenges the session, the apply will fail with a login redirect.
"""

from typing import Any

from loguru import logger

from agent.submitter.session_manager import SessionManager


class LinkedInApplyHandler:
    """Handle application submission on LinkedIn (Easy Apply)."""

    # TODO: Verify these selectors on live LinkedIn job pages.
    # LinkedIn updates their HTML regularly — check via headed browser
    # if these stop working.
    _SELECTORS = {
        # "Easy Apply" button on the job detail page
        "easy_apply_btn": (
            "button.jobs-apply-button[aria-label*='Easy Apply'], "
            "button[data-control-name='jobdetails_topcard_inapply'], "
            "button.artdeco-button--primary[aria-label*='Easy Apply']"
        ),
        # "Apply" button that redirects to external site (we cannot handle this)
        "external_apply_btn": (
            "button.jobs-apply-button[aria-label*='Apply']:not([aria-label*='Easy Apply']), "
            "a.jobs-apply-button"
        ),
        # The Easy Apply modal/panel
        "apply_modal": (
            "div.jobs-easy-apply-content, "
            "div[data-test-modal='easy-apply-modal'], "
            "div.artdeco-modal--is-top-partial"
        ),
        # Cover letter / additional info textarea
        "cover_letter": (
            "textarea[id*='coverLetter'], "
            "textarea[id*='additional-information'], "
            "div[data-test-form-element-label-title*='cover'] textarea, "
            "textarea[placeholder*='cover letter']"
        ),
        # "Next" / "Review" button between steps
        "next_btn": (
            "button[aria-label='Continue to next step'], "
            "button[data-control-name='continue_unify'], "
            "button[aria-label='Review your application']"
        ),
        # Final submit button
        "submit_btn": (
            "button[aria-label='Submit application'], "
            "button[data-control-name='submit_unify'], "
            "button[aria-label*='Submit']"
        ),
        # Post-submit success message
        "success_indicator": (
            "div[data-test-modal] h2[class*='applied'], "
            "h2.t-24:has-text('Application submitted'), "
            "div.jobs-apply-form-confirmation"
        ),
        # Dismiss / close button if we need to bail
        "close_btn": "button[aria-label='Dismiss']",
    }

    async def fill(
        self,
        page: Any,
        job: dict[str, Any],
        profile: dict[str, Any],
        cover_letter: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Attempt LinkedIn Easy Apply on the given job URL.

        Args:
            page: Ignored — this handler manages its own browser.
            job: Canonical job dict.
            profile: Profile dict loaded from profile.yaml.
            cover_letter: Generated cover letter string.
            dry_run: If True, navigate but do not submit.

        Returns:
            Result dict with ``status`` (applied | failed | dry_run) and ``error``.
        """
        from playwright.async_api import async_playwright  # type: ignore[import]

        async with async_playwright() as pw:
            # LinkedIn detects headless=True more aggressively;
            # use channel='chrome' if available for a more realistic UA.
            try:
                browser = await pw.chromium.launch(headless=True, channel="chrome")
            except Exception:  # noqa: BLE001
                browser = await pw.chromium.launch(headless=True)

            session_mgr = SessionManager()
            context = await session_mgr.load_session("linkedin", browser)
            session_page = await context.new_page()
            screenshot_company = job.get("company", "unknown")[:20]

            try:
                await session_page.goto(job["url"], wait_until="domcontentloaded", timeout=30_000)
                await session_page.wait_for_timeout(2500)  # let React finish rendering

                # ── Detect apply mode ─────────────────────────────────────────
                external = session_page.locator(self._SELECTORS["external_apply_btn"])
                easy_apply = session_page.locator(self._SELECTORS["easy_apply_btn"])

                if await easy_apply.count() == 0:
                    if await external.count() > 0:
                        return {
                            "status": "failed",
                            "error": "LinkedIn: only external apply available — no Easy Apply button",
                        }
                    return {
                        "status": "failed",
                        "error": "LinkedIn: no apply button found — session may be stale or page changed",
                    }

                await easy_apply.first.click()

                # Wait for the modal to appear
                try:
                    await session_page.wait_for_selector(
                        self._SELECTORS["apply_modal"], timeout=8_000
                    )
                except Exception:  # noqa: BLE001
                    pass  # proceed; some flows skip an explicit modal element

                # ── Fill cover letter if present ──────────────────────────────
                cl_area = session_page.locator(self._SELECTORS["cover_letter"])
                if await cl_area.count() > 0:
                    await cl_area.first.fill(cover_letter[:2000])

                if dry_run:
                    logger.info(f"LinkedInApply: dry_run=True — not submitting for {job.get('company')}")
                    # Close modal cleanly
                    close = session_page.locator(self._SELECTORS["close_btn"])
                    if await close.count() > 0:
                        await close.first.click()
                    return {"status": "dry_run", "error": ""}

                # ── Step through multi-page form (up to 6 steps) ─────────────
                for _step in range(6):
                    await session_page.wait_for_timeout(1000)

                    # Fill cover letter on each step (it may appear on any step)
                    cl = session_page.locator(self._SELECTORS["cover_letter"])
                    if await cl.count() > 0 and not await cl.first.input_value():
                        await cl.first.fill(cover_letter[:2000])

                    submit = session_page.locator(self._SELECTORS["submit_btn"])
                    if await submit.count() > 0:
                        await submit.first.click()
                        break

                    next_btn = session_page.locator(self._SELECTORS["next_btn"])
                    if await next_btn.count() > 0:
                        await next_btn.first.click()
                    else:
                        # No next and no submit — stuck on a screening question
                        logger.warning(
                            f"LinkedInApply: stuck on form step {_step + 1} "
                            f"for {job.get('company')} — no next/submit button"
                        )
                        return {
                            "status": "failed",
                            "error": f"Stuck on form step {_step + 1}: no next/submit button (screening questions?)",
                        }

                await session_page.wait_for_timeout(2000)

                # ── Check success indicator ───────────────────────────────────
                success = session_page.locator(self._SELECTORS["success_indicator"])
                if await success.count() == 0:
                    logger.warning(
                        f"LinkedInApply: success indicator not found for {job.get('company')} — "
                        "application may not have submitted"
                    )

                logger.info(f"LinkedInApply: applied to {job.get('company')} — {job.get('title')}")
                return {"status": "applied", "error": ""}

            except Exception as exc:  # noqa: BLE001
                logger.error(f"LinkedInApplyHandler error: {exc}")
                return {"status": "failed", "error": str(exc)}
            finally:
                try:
                    await session_page.screenshot(
                        path=f"data/screenshots/linkedin_{screenshot_company}.png"
                    )
                except Exception:  # noqa: BLE001
                    pass
                await context.close()
                await browser.close()
