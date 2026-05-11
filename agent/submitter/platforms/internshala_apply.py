"""
agent/submitter/platforms/internshala_apply.py — Internshala apply flow.

Uses the saved Internshala session to submit applications.

Internshala apply flow (observed 2026):
  1. Listing page has a blue "Apply now" button (a.btn or button).
  2. Clicking it opens a modal (#apply-modal or similar).
  3. The modal contains a cover letter textarea that starts HIDDEN — it's
     inside a collapsible section.  Must scroll to it / make visible before fill.
  4. A "Submit" button closes the modal and submits.
  5. A green "Applied successfully" toast confirms success.
"""

from typing import Any

from loguru import logger

from agent.submitter.session_manager import SessionManager

# ── Apply button on listing page ──────────────────────────────────────────────
_APPLY_BTN = [
    "button#apply_button",
    "a#apply_button",
    "button.btn-primary:has-text('Apply now')",
    "a.btn-primary:has-text('Apply now')",
    "button:has-text('Apply now')",
    "a:has-text('Apply now')",
]

# ── Modal container — wait for this after clicking apply ──────────────────────
_MODAL_SEL = (
    "div#apply-modal.show, "
    "div.modal.show, "
    "div[id*='apply'].show, "
    "div.application-form"
)

# ── Cover letter toggle — some modals hide the textarea behind a link ─────────
_CL_TOGGLE = [
    "a:has-text('cover letter')",
    "button:has-text('cover letter')",
    "span:has-text('Write a cover letter')",
    "a:has-text('Write')",
]

# ── Cover letter textarea ─────────────────────────────────────────────────────
_CL_AREA = [
    "textarea#cover_letter",
    "textarea[name='cover_letter']",
    "textarea.cover_letter",
    "textarea",
]

# ── Submit button inside modal ────────────────────────────────────────────────
_SUBMIT_BTN = [
    "input#submit",
    "input[type='submit']",
    "button#submit",
    "button[type='submit']:has-text('Submit')",
    "button.btn-primary[type='submit']",
    "button:has-text('Submit')",
    ".submit_button_container input",
    ".submit_button_container button",
    ".easy_apply_footer input",
    ".easy_apply_footer button",
]

# ── Success indicator ─────────────────────────────────────────────────────────
_SUCCESS = [
    "div.success-alert",
    "div.alert-success",
    "div.application-success",
    "div:has-text('Applied successfully')",
    "div:has-text('applied successfully')",
    "div:has-text('Application submitted')",
    "div:has-text('successfully applied')",
    "div:has-text('successfully submitted')",
    "div.toast:has-text('success')",
    "p:has-text('application has been submitted')",
    "h3:has-text('applied')",
    "h4:has-text('applied')",
    "div.modal-body:has-text('thank you')",
    "div:has-text('Thank you for applying')",
]


async def _fill_modal_extras(page: Any, profile: dict) -> None:
    """Fill additional required fields inside the Internshala apply modal.

    Internshala uses Chosen.js for styled selects:
      <select class="chosen-select" style="display:none" id="custom_question_range_N">
      <div class="chosen-container" id="custom_question_range_N_chosen">
        <a class="chosen-single">  ← click to open
        <ul class="chosen-results"> ← items appear here

    The only reliable way to drive Chosen is to click the trigger div,
    wait for the results list to populate, then click the target <li>.
    Programmatic select_option() + change dispatch alone does NOT update
    Chosen's validation state, hence the field stays "required" and Submit fails.
    """
    links = profile.get("links") or {}
    portfolio_url = links.get("portfolio") or links.get("github") or "N/A"
    github_url = links.get("github") or portfolio_url

    async def _label_of(el: Any) -> str:
        """Return nearest question label text (lowercase)."""
        for ancestor_xpath in [
            "xpath=ancestor::div[contains(@class,'form-group')][1]",
            "xpath=ancestor::div[1]",
        ]:
            try:
                parent = el.locator(ancestor_xpath)
                for lbl_sel in ["label", "p.question", "div.question"]:
                    lbl = parent.locator(lbl_sel)
                    if await lbl.count() > 0:
                        txt = (await lbl.first.text_content() or "").strip().lower()
                        if txt:
                            return txt
            except Exception:  # noqa: BLE001
                pass
        return ""

    async def _best_url(label_text: str) -> str:
        return github_url if "github" in label_text else portfolio_url

    # ── Chosen.js selects (class="chosen-select") ────────────────────────────
    # Pattern on Internshala:
    #   native <select id="custom_question_range_N"> → hidden by Chosen
    #   Chosen container <div id="custom_question_range_N_chosen">
    #     └─ <a class="chosen-single">  ← click trigger
    #        <div class="chosen-drop">
    #          <ul class="chosen-results"> ← <li> items appear after click
    try:
        chosen_selects = page.locator("select.chosen-select")
        cs_count = await chosen_selects.count()
        for i in range(cs_count):
            try:
                sel_el = chosen_selects.nth(i)

                # Skip if already has a real value
                try:
                    current_val = await sel_el.input_value()
                    if current_val and current_val not in ("", "Select Range"):
                        continue
                except Exception:  # noqa: BLE001
                    pass

                # Determine target option value — for 1-5 scale pick "4"
                options = sel_el.locator("option:not([disabled])")
                opt_count = await options.count()
                if opt_count == 0:
                    continue
                if opt_count <= 5:
                    target_opt_idx = opt_count - 2   # second-to-last (e.g. "4" of 1-5)
                else:
                    target_opt_idx = max(0, (opt_count * 3) // 4)
                target_opt_idx = max(0, target_opt_idx)
                target_val = (await options.nth(target_opt_idx).get_attribute("value") or "").strip()
                target_txt = (await options.nth(target_opt_idx).text_content() or "").strip()
                if not target_val:
                    target_val = target_txt

                sel_id = await sel_el.get_attribute("id") or ""
                chosen_id = f"{sel_id}_chosen" if sel_id else ""

                filled = False

                # Method A: click Chosen trigger → wait for results → click item
                try:
                    trigger_sel = (
                        f"#{chosen_id} a.chosen-single"
                        if chosen_id
                        else "div.chosen-container a.chosen-single"
                    )
                    trigger = page.locator(trigger_sel).nth(i if not chosen_id else 0)
                    await trigger.click(timeout=3_000)
                    # Wait for Chosen to populate the results list
                    results_sel = (
                        f"#{chosen_id} ul.chosen-results li"
                        if chosen_id
                        else "div.chosen-drop ul.chosen-results li"
                    )
                    await page.wait_for_selector(results_sel, timeout=3_000, state="visible")
                    # Click the item whose text matches target_txt
                    item = page.locator(f"{results_sel}:has-text('{target_txt}')").first
                    if await item.count() == 0:
                        # Fall back to positional
                        item = page.locator(results_sel).nth(target_opt_idx)
                    await item.click(timeout=2_000)
                    await page.wait_for_timeout(300)
                    filled = True
                    logger.debug(
                        f"InternshalaApply: Chosen select[{i}] → clicked '{target_txt}'"
                    )
                except Exception:  # noqa: BLE001
                    pass

                # Method B: jQuery trigger (Internshala loads jQuery)
                if not filled and sel_id:
                    try:
                        result = await page.evaluate(
                            """([id, val]) => {
                                const sel = document.getElementById(id);
                                if (!sel) return false;
                                sel.value = val;
                                // Chosen listens for 'change' on the native select
                                sel.dispatchEvent(new Event('change', {bubbles: true}));
                                // Also trigger chosen:updated so the visible UI refreshes
                                if (window.jQuery) {
                                    jQuery(sel).trigger('chosen:updated');
                                }
                                return sel.value;
                            }""",
                            [sel_id, target_val],
                        )
                        if result and result not in ("", "Select Range"):
                            filled = True
                            logger.debug(
                                f"InternshalaApply: Chosen select[{i}] → jQuery set '{result}'"
                            )
                    except Exception:  # noqa: BLE001
                        pass

                if not filled:
                    logger.warning(
                        f"InternshalaApply: could not fill Chosen select[{i}] (id={sel_id!r})"
                    )
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass

    # ── Plain (non-Chosen) selects ────────────────────────────────────────────
    try:
        plain_selects = page.locator("select:not(.chosen-select)")
        ps_count = await plain_selects.count()
        for i in range(ps_count):
            try:
                sel_el = plain_selects.nth(i)
                current_val = await sel_el.input_value()
                if current_val and current_val not in ("", "0", "null"):
                    continue
                options = sel_el.locator("option:not([disabled])")
                opt_count = await options.count()
                if opt_count == 0:
                    continue
                target_idx = max(0, opt_count - 2) if opt_count <= 5 else max(0, (opt_count * 3) // 4)
                opt_val = (await options.nth(target_idx).get_attribute("value") or "").strip()
                if opt_val:
                    await sel_el.select_option(value=opt_val)
                    await sel_el.dispatch_event("change")
                    logger.debug(f"InternshalaApply: plain select[{i}] → '{opt_val}'")
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass

    # ── Textareas (skip cover letter if already filled) ────────────────────────
    try:
        textareas = page.locator("textarea")
        ta_count = await textareas.count()
        for i in range(ta_count):
            try:
                ta = textareas.nth(i)
                current_val = await ta.input_value()
                if current_val:
                    continue  # already filled (cover letter or previous pass)
                if not await ta.is_visible(timeout=500):
                    await ta.scroll_into_view_if_needed(timeout=2_000)
                label_text = await _label_of(ta)
                fill_val = await _best_url(label_text)
                await ta.fill(fill_val)
                logger.debug(f"InternshalaApply: filled textarea[{i}] (label≈'{label_text[:50]}')")
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass

    # ── Text / URL inputs (skip hidden, already-filled, buttons) ─────────────
    try:
        inputs = page.locator("input[type='text'], input[type='url'], input:not([type])")
        inp_count = await inputs.count()
        for i in range(inp_count):
            try:
                inp = inputs.nth(i)
                if not await inp.is_visible(timeout=300):
                    continue
                inp_type = (await inp.get_attribute("type") or "text").lower()
                if inp_type in ("submit", "button", "reset", "file", "hidden", "checkbox", "radio"):
                    continue
                current_val = await inp.input_value()
                if current_val:
                    continue
                label_text = await _label_of(inp)
                fill_val = await _best_url(label_text)
                await inp.fill(fill_val)
                logger.debug(f"InternshalaApply: filled input[{i}] (label≈'{label_text[:50]}')")
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


class InternshalaApplyHandler:
    """Handle application submission on Internshala.com."""

    async def fill(
        self,
        page: Any,
        job: dict[str, Any],
        profile: dict[str, Any],
        cover_letter: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Submit an application on Internshala.com."""
        from playwright.async_api import async_playwright  # type: ignore[import]

        company = job.get("company", "unknown")
        sc_name = company[:20].replace("/", "_")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False)
            session_mgr = SessionManager()
            context = await session_mgr.load_session("internshala", browser)
            session_page = await context.new_page()

            try:
                original_url = job["url"]
                await session_page.goto(
                    original_url, wait_until="domcontentloaded", timeout=30_000
                )
                await session_page.wait_for_timeout(1_500)
                # ── Detect already-applied ──────────────────────────────────────
                _ALREADY_APPLIED = [
                    "button:has-text('Already Applied')",
                    "a:has-text('Already Applied')",
                    "span:has-text('You have already applied')",
                    "div.already-applied",
                ]
                for aa_sel in _ALREADY_APPLIED:
                    try:
                        if await session_page.locator(aa_sel).count() > 0:
                            logger.info(f"InternshalaApply: already applied to {company}")
                            return {"status": "applied", "error": ""}
                    except Exception:  # noqa: BLE001
                        pass

                # Scroll to bottom to reveal apply button on long pages
                await session_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await session_page.wait_for_timeout(500)
                await session_page.evaluate("window.scrollTo(0, 0)")
                await session_page.wait_for_timeout(300)
                # ── Click Apply now ───────────────────────────────────────────
                clicked = False
                for sel in _APPLY_BTN:
                    try:
                        btn = session_page.locator(sel).first
                        if await btn.is_visible(timeout=1_500):
                            await btn.click()
                            clicked = True
                            logger.debug(f"InternshalaApply: clicked apply via '{sel}'")
                            break
                    except Exception:  # noqa: BLE001
                        pass

                if not clicked:
                    await session_page.screenshot(
                        path=f"data/screenshots/internshala_{sc_name}_noapply.png"
                    )
                    return {"status": "failed", "error": "Apply button not found"}

                # ── Wait for modal to open ────────────────────────────────────
                try:
                    await session_page.wait_for_selector(_MODAL_SEL, timeout=6_000)
                except Exception:  # noqa: BLE001
                    pass  # modal may not match selector — proceed anyway
                await session_page.wait_for_timeout(1_000)

                # ── Reveal cover letter if it's collapsed ─────────────────────
                for toggle_sel in _CL_TOGGLE:
                    try:
                        toggle = session_page.locator(toggle_sel).first
                        if await toggle.is_visible(timeout=800):
                            await toggle.click()
                            await session_page.wait_for_timeout(600)
                            logger.debug("InternshalaApply: clicked cover letter toggle")
                            break
                    except Exception:  # noqa: BLE001
                        pass

                # ── Fill cover letter ─────────────────────────────────────────
                for cl_sel in _CL_AREA:
                    try:
                        cl = session_page.locator(cl_sel).first
                        # scroll into view in case it's off-screen inside modal
                        await cl.scroll_into_view_if_needed(timeout=3_000)
                        if await cl.is_visible(timeout=2_000):
                            await cl.fill(cover_letter)
                            logger.debug(f"InternshalaApply: filled cover letter via '{cl_sel}'")
                            break
                    except Exception:  # noqa: BLE001
                        pass

                # ── Additional required fields (selects, extra textareas) ─────────
                await _fill_modal_extras(session_page, profile)

                if dry_run:
                    logger.info("InternshalaApply: dry_run — not submitting")
                    return {"status": "dry_run", "error": ""}

                # ── Submit ────────────────────────────────────────────────────
                submitted = False
                for sub_sel in _SUBMIT_BTN:
                    try:
                        sub = session_page.locator(sub_sel).first
                        if await sub.count() == 0:
                            continue
                        await sub.scroll_into_view_if_needed(timeout=2_000)
                        await sub.click(timeout=5_000)
                        submitted = True
                        logger.debug(f"InternshalaApply: clicked submit via '{sub_sel}'")
                        break
                    except Exception:  # noqa: BLE001
                        pass

                # Last resort: force-click input#submit even if obscured
                if not submitted:
                    try:
                        sub = session_page.locator("input#submit, input[type='submit']").first
                        if await sub.count() > 0:
                            await sub.click(force=True, timeout=5_000)
                            submitted = True
                            logger.debug("InternshalaApply: force-clicked submit button")
                    except Exception:  # noqa: BLE001
                        pass

                if not submitted:
                    await session_page.screenshot(
                        path=f"data/screenshots/internshala_{sc_name}_nosubmit.png"
                    )
                    return {"status": "failed", "error": "Submit button not found"}

                # ── Wait for modal to close or page to react (up to 8s) ────
                try:
                    await session_page.wait_for_selector(
                        _MODAL_SEL, state="hidden", timeout=8_000
                    )
                    logger.debug("InternshalaApply: modal closed after submit — success")
                    modal_closed = True
                except Exception:  # noqa: BLE001
                    modal_closed = False

                await session_page.wait_for_timeout(2_000)

                # ── Check success ─────────────────────────────────────────────
                # 1) Modal closed = AJAX submit succeeded
                confirmed = modal_closed

                # 2) DOM toast/alert
                if not confirmed:
                    for succ_sel in _SUCCESS:
                        try:
                            if await session_page.locator(succ_sel).count() > 0:
                                confirmed = True
                                break
                        except Exception:  # noqa: BLE001
                            pass

                # 3) URL changed away from the original job listing (redirect = success)
                if not confirmed:
                    current_url = session_page.url
                    if current_url and current_url != original_url:
                        logger.debug(
                            f"InternshalaApply: URL changed after submit → '{current_url[:60]}' — treating as success"
                        )
                        confirmed = True

                if not confirmed:
                    logger.warning(
                        f"InternshalaApply: no success indicator for {company} — marking failed"
                    )
                    return {
                        "status": "failed",
                        "error": "no confirmation signal after submit",
                    }

                logger.info(f"InternshalaApply: applied to {company} — {job.get('title')}")
                return {"status": "applied", "error": ""}

            except Exception as exc:  # noqa: BLE001
                logger.error(f"InternshalaApplyHandler.fill error: {exc}")
                return {"status": "failed", "error": str(exc)}
            finally:
                try:
                    await session_page.screenshot(
                        path=f"data/screenshots/internshala_{sc_name}.png"
                    )
                except Exception:  # noqa: BLE001
                    pass
                await session_page.close()
                await context.close()
                await browser.close()
