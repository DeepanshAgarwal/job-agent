"""
agent/submitter/platforms/indeed_apply.py — Indeed Quick Apply handler.

Indeed's apply flow (as of 2026):
  1. Job listing page has an "Apply now" / "Apply with Indeed" button.
  2. Clicking it opens a NEW TAB at smartapply.indeed.com (not a modal).
  3. The new tab shows a resume-review step with a progress bar.
  4. Clicking "Continue" advances through 1-N question steps.
  5. The final step has a "Submit your application" button.
  6. A success/confirmation screen appears after submit.

If instead of the Quick Apply button the listing shows "Apply on company
site", we return ``failed`` — those require a company-specific session.

Session file: auth/indeed_session.json
Capture with: python setup.py --login indeed
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from agent.submitter.session_manager import SessionManager

_BASE_URL = "https://in.indeed.com"

# ── Selectors on the JOB LISTING page ────────────────────────────────────────
_LISTING_APPLY_BTN = (
    "button#indeedApplyButton, "
    "button.ia-IndeedApplyButton, "
    "span[id='indeedApplyButtonContainer'] button, "
    "div.jobsearch-IndeedApplyButton--single button, "
    "button[data-tn-element='applyButton'], "
    "button[data-indeed-apply-jobid], "
    "button:has-text('Apply now'), "
    "button:has-text('Apply with Indeed'), "
    "button:has-text('Apply on Indeed')"
)

_LISTING_EXTERNAL_BTN = (
    "a[id='applyButtonLinkContainer'], "
    "a.jobsearch-SerpJob-externalApplyLink, "
    "div#applyButtonLinkContainer a, "
    "a:has-text('Apply on company site')"
)

# ── Selectors on the APPLY TAB (smartapply.indeed.com) ───────────────────────
_APPLY_CONTINUE = [
    "button[data-testid='ia-continueButton']",
    "button[data-testid='continue-button']",
    "button[aria-label*='Continue']",
    "button:has-text('Continue')",
    "button:has-text('Next')",
    "button:has-text('Proceed')",
]

_APPLY_SUBMIT = [
    "button[data-testid='ia-SubmitButton']",
    "button[data-testid='submit-button']",
    "button:has-text('Submit your application')",
    "button:has-text('Submit application')",
    "button:has-text('Submit')",
]

_APPLY_SUCCESS = [
    "[data-testid='ia-SuccessPage']",
    "div.ia-PostApply",
    "h1:has-text('application was sent')",
    "h1:has-text('Application submitted')",
    "h2:has-text('Your application')",
    "div:has-text('Thank you for applying')",
    "p:has-text('application was sent')",
]

# Label substrings → profile value key (lower-cased, checked with 'in')
# Earlier entries win — keep specific phrases before generic ones.
_LABEL_TO_KEY: list[tuple[tuple[str, ...], str]] = [
    (("phone", "mobile", "contact number"),          "phone"),
    (("current job title", "current title",
      "present designation", "job title"),            "current_title"),
    (("current company", "present employer",
      "current employer", "current organization",
      "company"),                                     "current_company"),
    (("years of experience", "total experience",
      "work experience", "experience in years"),      "years_of_experience"),
    (("notice period", "notice"),                     "notice_period"),
    (("current ctc", "current salary",
      "current compensation", "current package"),     "current_ctc"),
    (("expected ctc", "expected salary",
      "expected compensation", "expected package"),   "expected_ctc"),
    (("linkedin",),                                   "linkedin"),
    (("github",),                                     "github"),
    (("portfolio", "website"),                        "portfolio"),
    (("cover letter",),                               "cover_letter"),
    (("start date", "start month", "from month",
      "from date", "joining date"),                   "work_start"),
    (("start year", "from year"),                     "work_start_year"),
    (("end date", "end month", "to month", "to date"),"work_end"),
    (("end year", "to year"),                         "work_end_year"),
]


def _build_values(profile: dict, cover_letter: str) -> dict[str, str]:
    """Flatten profile into a key→value map for form filling."""
    personal = profile.get("personal", {})
    links = profile.get("links", {})
    answers = profile.get("standard_answers", {})
    return {
        "phone":                str(personal.get("phone", "")),
        "current_title":        "Software Engineer",
        "current_company":      str(answers.get("current_company", "")),
        "years_of_experience":  str(answers.get("years_of_experience", "2")),
        "notice_period":        str(answers.get("notice_period", "30 days")),
        "current_ctc":          str(answers.get("current_ctc", "")),
        "expected_ctc":         str(answers.get("expected_ctc", "")),
        "linkedin":             links.get("linkedin", ""),
        "github":               links.get("github", ""),
        "portfolio":            links.get("portfolio", ""),
        "cover_letter":         cover_letter[:2000],
        "work_start":           "January",
        "work_start_year":      "2022",
        "work_end":             "Present",
        "work_end_year":        "",
    }


def _match_label(label_text: str) -> str | None:
    """Return the profile key for a form label string, or None."""
    low = label_text.lower().strip()
    for phrases, key in _LABEL_TO_KEY:
        if any(p in low for p in phrases):
            return key
    return None


class IndeedApplyHandler:
    """Handle application submission via Indeed Quick Apply (new-tab flow)."""

    async def fill(
        self,
        page: Any,
        job: dict[str, Any],
        profile: dict[str, Any],
        cover_letter: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Attempt Indeed Quick Apply for *job*.

        Args:
            page: Ignored — this handler manages its own browser context.
            job:  Canonical job dict.
            profile: Profile dict from profile.yaml.
            cover_letter: Generated cover letter string.
            dry_run: If True, navigate + fill but do not click Submit.

        Returns:
            ``{"status": "applied"|"failed"|"dry_run", "error": "..."}``
        """
        from playwright.async_api import async_playwright  # type: ignore[import]

        values = _build_values(profile, cover_letter)
        company = job.get("company", "unknown")
        sc_name = company[:20].replace("/", "_")

        async with async_playwright() as pw:
            # headless=False required — Indeed's bot detection blocks headless
            # browsers even with valid session cookies.
            try:
                browser = await pw.chromium.launch(headless=False, channel="chrome")
            except Exception:  # noqa: BLE001
                browser = await pw.chromium.launch(headless=False)

            session_mgr = SessionManager()
            context = await session_mgr.load_session("indeed", browser)
            listing_page = await context.new_page()

            try:
                # ── 1. Load job listing page ──────────────────────────────────
                await listing_page.goto(
                    job["url"], wait_until="domcontentloaded", timeout=30_000
                )
                await listing_page.wait_for_timeout(2_500)

                # ── 2. Detect apply mode ──────────────────────────────────────
                if await listing_page.locator(_LISTING_EXTERNAL_BTN).count() > 0:
                    return {
                        "status": "failed",
                        "error": "Indeed: 'Apply on company site' — external ATS, cannot auto-apply",
                    }

                apply_btn = listing_page.locator(_LISTING_APPLY_BTN)
                if await apply_btn.count() == 0:
                    await listing_page.screenshot(
                        path=f"data/screenshots/indeed_{sc_name}_notfound.png"
                    )
                    logger.debug(f"IndeedApply: no apply button for {company}")
                    return {
                        "status": "failed",
                        "error": "Indeed: apply button not found — page changed or login stale",
                    }

                # ── 3. Click → capture new tab ────────────────────────────────
                logger.debug(f"IndeedApply: clicking apply button for {company}")
                try:
                    async with context.expect_page(timeout=12_000) as new_page_info:
                        await apply_btn.first.click()
                    apply_page = await new_page_info.value
                    await apply_page.wait_for_load_state("domcontentloaded", timeout=20_000)
                    logger.debug(f"IndeedApply: apply tab opened → {apply_page.url[:80]}")
                except Exception:  # noqa: BLE001
                    # No new tab — Indeed may have opened form in same page
                    logger.debug("IndeedApply: no new tab detected, using same page")
                    apply_page = listing_page

                # ── 4. Walk the multi-step apply form ─────────────────────────
                return await self._walk_apply_form(
                    apply_page, values, company, sc_name, dry_run
                )

            except Exception as exc:  # noqa: BLE001
                logger.error(f"IndeedApplyHandler error for {company}: {exc}")
                return {"status": "failed", "error": str(exc)}
            finally:
                try:
                    await listing_page.screenshot(
                        path=f"data/screenshots/indeed_{sc_name}_final.png"
                    )
                except Exception:  # noqa: BLE001
                    pass
                await context.close()
                await browser.close()

    async def _walk_apply_form(
        self,
        page: Any,
        values: dict[str, str],
        company: str,
        sc_name: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Walk through Indeed's multi-step apply form.

        Each iteration: wait for content → check success → check submit → fill fields → click Continue.
        """
        for step in range(12):
            # Wait for the step content to render — Indeed loads each step
            # asynchronously. First wait for any interactive element, then
            # wait specifically for a named button (Continue/Submit) so we
            # don't proceed while the button is showing a loading spinner.
            try:
                await page.wait_for_selector(
                    "button, input:not([type='hidden']), textarea, select",
                    timeout=15_000,
                    state="visible",
                )
            except Exception:  # noqa: BLE001
                pass
            # Ensure the spinner has resolved — wait for a real button label.
            try:
                await page.wait_for_selector(
                    "button:has-text('Continue'), button:has-text('Next'), "
                    "button:has-text('Submit'), button:has-text('Proceed')",
                    timeout=10_000,
                    state="visible",
                )
            except Exception:  # noqa: BLE001
                pass
            await page.wait_for_timeout(600)  # final React settle

            # ── Success screen? ───────────────────────────────────────────
            for sel in _APPLY_SUCCESS:
                try:
                    if await page.locator(sel).count() > 0:
                        logger.info(f"IndeedApply: applied to {company} (step {step})")
                        return {"status": "applied", "error": ""}
                except Exception:  # noqa: BLE001
                    pass

            # ── Screenshot for debugging ──────────────────────────────────
            try:
                await page.screenshot(
                    path=f"data/screenshots/indeed_{sc_name}_step{step}.png"
                )
            except Exception:  # noqa: BLE001
                pass

            # ── Submit button? ────────────────────────────────────────────
            # The review/final step can take several seconds to render the
            # "Submit your application" button. Give it up to 12 s before
            # falling through to the Continue / stuck logic.
            _submit_combined = ", ".join(_APPLY_SUBMIT)
            try:
                await page.wait_for_selector(
                    _submit_combined, timeout=12_000, state="visible"
                )
            except Exception:  # noqa: BLE001
                pass

            submitted = False
            for sel in _APPLY_SUBMIT:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=500):
                        if dry_run:
                            logger.info(
                                f"IndeedApply: dry_run — found Submit at step {step}"
                            )
                            return {"status": "dry_run", "error": ""}
                        logger.debug(f"IndeedApply: clicking Submit at step {step}")
                        await btn.click()
                        await page.wait_for_timeout(4_000)
                        submitted = True
                        break
                except Exception:  # noqa: BLE001
                    pass
            if submitted:
                continue  # loop to check success screen

            # ── Fill visible form fields ──────────────────────────────────
            await self._fill_step_fields(page, values)

            # ── Continue / Next ───────────────────────────────────────────
            # Similarly wait for Continue to fully render before checking.
            _continue_combined = ", ".join(_APPLY_CONTINUE)
            try:
                await page.wait_for_selector(
                    _continue_combined, timeout=8_000, state="visible"
                )
            except Exception:  # noqa: BLE001
                pass

            advanced = False
            for sel in _APPLY_CONTINUE:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=500):
                        logger.debug(f"IndeedApply: clicking Continue at step {step}")
                        await btn.click()
                        advanced = True
                        break
                except Exception:  # noqa: BLE001
                    pass

            if not advanced:
                await page.screenshot(
                    path=f"data/screenshots/indeed_{sc_name}_stuck_step{step}.png"
                )
                logger.warning(
                    f"IndeedApply: stuck at step {step} for {company} — "
                    "no Continue or Submit found"
                )
                return {
                    "status": "failed",
                    "error": f"Indeed: stuck at form step {step} — no Continue or Submit visible",
                }

        return {
            "status": "failed",
            "error": "Indeed: exhausted 12 form steps without reaching success screen",
        }

    async def _fill_step_fields(self, page: Any, values: dict[str, str]) -> None:
        """Fill all visible text/textarea inputs on the current step using label matching."""
        try:
            labels = page.locator("label")
            count = await labels.count()
            for i in range(min(count, 20)):
                lbl = labels.nth(i)
                try:
                    if not await lbl.is_visible(timeout=200):
                        continue
                    label_text = (await lbl.text_content() or "").strip()
                except Exception:  # noqa: BLE001
                    continue

                key = _match_label(label_text)
                if key is None:
                    continue
                value = values.get(key, "")
                if not value:
                    continue

                # Prefer for= attribute → find input/select by id
                for_id = await lbl.get_attribute("for")
                if for_id:
                    target = page.locator(f"#{for_id}")
                    if await target.count() > 0:
                        tag = await target.first.evaluate("el => el.tagName.toLowerCase()")
                        if tag == "select":
                            await self._fill_select(target.first, value)
                        else:
                            await self._fill_element(target.first, value)
                        continue

                # Fallback: nearest input/textarea/select inside label's parent
                parent = lbl.locator("xpath=..")
                selects = parent.locator("select")
                if await selects.count() > 0:
                    await self._fill_select(selects.first, value)
                    continue
                inputs = parent.locator(
                    "input:not([type='hidden']):not([type='submit']):not([type='radio'])"
                    ":not([type='checkbox']), textarea"
                )
                if await inputs.count() > 0:
                    await self._fill_element(inputs.first, value)

        except Exception as exc:  # noqa: BLE001
            logger.debug(f"IndeedApply: field-fill error: {exc}")

        # Handle Yes/No radio groups (work authorization etc.)
        await self._answer_yes_no_radios(page)

    @staticmethod
    async def _fill_element(el: Any, value: str) -> None:
        """Fill a single input or textarea element."""
        try:
            input_type = (await el.get_attribute("type") or "text").lower()
            if input_type in ("submit", "button", "reset", "file", "checkbox", "radio"):
                return
            await el.fill(str(value))
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"IndeedApply: could not fill element: {exc}")

    @staticmethod
    async def _fill_select(el: Any, value: str) -> None:
        """Select an option from a <select> element by value, label, or index."""
        try:
            if not value:
                # pick the first non-placeholder option
                options = el.locator("option")
                cnt = await options.count()
                if cnt > 1:
                    val = await options.nth(1).get_attribute("value")
                    if val:
                        await el.select_option(value=val)
                return
            try:
                await el.select_option(value=value)
            except Exception:  # noqa: BLE001
                try:
                    await el.select_option(label=value)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"IndeedApply: could not select option: {exc}")

    @staticmethod
    async def _answer_yes_no_radios(page: Any) -> None:
        """For work-authorization and similar Yes/No fieldsets, select 'Yes'."""
        _YES_TRIGGERS = ("authorized", "legally", "eligible", "citizen", "resident", "work in india")
        try:
            groups = page.locator("fieldset")
            count = await groups.count()
            for i in range(min(count, 10)):
                group = groups.nth(i)
                legend_el = group.locator("legend")
                if await legend_el.count() == 0:
                    continue
                legend = (await legend_el.first.text_content() or "").lower()
                if not any(kw in legend for kw in _YES_TRIGGERS):
                    continue
                radios = group.locator("input[type='radio']")
                if await radios.count() > 0:
                    try:
                        await radios.first.check()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
