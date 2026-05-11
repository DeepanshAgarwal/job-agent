"""
agent/submitter/platforms/generic.py — AI-guided fallback form filler.

When no specific platform handler matches the job URL, this handler
uses Google Gemini to analyse the page HTML and identify form fields,
then fills them intelligently.  This is a best-effort approach and may
not work perfectly on all sites.
"""

import json
from typing import Any

from loguru import logger


class GenericHandler:
    """AI-guided fallback form filler for unknown ATS platforms."""

    _PROMPT = """
You are a form-filling assistant.  Given the HTML of a job application page,
identify the form fields and return a JSON array of fill instructions.

Each instruction must have:
  - "selector": CSS selector string
  - "type": "text" | "email" | "tel" | "textarea" | "file" | "select"
  - "value": the value to fill (use PLACEHOLDER_RESUME for file uploads)

Available candidate data:
{candidate_data}

Page HTML (first 5000 chars):
{html}

Return ONLY a valid JSON array, no markdown.
"""

    def __init__(self) -> None:
        """Initialise the Gemini client using the shared cached client."""
        from agent.ai._gemini import get_client_and_model
        self._client, self._model_name = get_client_and_model()
        if self._client is None:
            logger.warning("GenericHandler: Gemini unavailable — AI-guided form filling disabled.")

    async def fill(
        self,
        page: Any,
        job: dict[str, Any],
        profile: dict[str, Any],
        cover_letter: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Navigate to *job* URL and attempt AI-guided form filling.

        Args:
            page: A Playwright ``Page`` instance.
            job: Canonical job dict.
            profile: Profile dict loaded from profile.yaml.
            cover_letter: Generated cover letter string.
            dry_run: If True, fill but do not submit.

        Returns:
            Result dict with ``status`` and ``error`` keys.
        """
        personal = profile.get("personal", {})
        resume_path = profile.get("resume", {}).get("path", "config/resume.pdf")
        links = profile.get("links", {})

        candidate_data = {
            "first_name": personal.get("first_name", ""),
            "last_name": personal.get("last_name", ""),
            "email": personal.get("email", ""),
            "phone": personal.get("phone", ""),
            "linkedin": links.get("linkedin", ""),
            "cover_letter": cover_letter[:500],
        }

        try:
            await page.goto(job["url"], wait_until="domcontentloaded", timeout=30_000)

            # ── Login-wall detection ─────────────────────────────────────────
            # If the ATS redirected us to a login/SSO/auth page we cannot
            # apply — bail early rather than filling a login form by mistake.
            final_url = page.url.lower()
            _login_signals = ("login", "signin", "sign-in", "sso", "auth", "saml", "oauth")
            if any(s in final_url for s in _login_signals):
                logger.warning(
                    f"Generic: login wall detected for {job.get('company')} "
                    f"(redirected to {page.url[:80]})"
                )
                return {"status": "manual_apply", "error": "login wall — ATS requires authentication"}

            # Also detect login pages that didn't change the URL (hidden form check)
            password_input = page.locator("input[type='password']")
            if await password_input.count() > 0:
                logger.warning(
                    f"Generic: password field found on {job.get('company')} page — login form detected"
                )
                return {"status": "manual_apply", "error": "login wall — ATS requires authentication"}

            html = await page.content()

            instructions = self._get_fill_instructions(html, candidate_data)
            if not instructions and self._client is not None:
                # Gemini is available but returned nothing — page structure unrecognised
                logger.warning(f"Generic: no fill instructions for {job.get('company')} — skipping")
                return {"status": "manual_apply", "error": "Gemini returned no fill instructions"}

            for instr in instructions:
                await self._execute_instruction(page, instr, resume_path)

            if dry_run:
                logger.info("Generic: dry_run=True — not submitting")
                return {"status": "dry_run", "error": ""}

            # ── Dismiss overlays that may intercept clicks ───────────────────
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
            for close_sel in [
                "[aria-label*='close' i]", "[aria-label*='dismiss' i]",
                "button.close", ".modal-close", "[data-dismiss='modal']",
            ]:
                try:
                    btn = page.locator(close_sel).first
                    if await btn.is_visible(timeout=500):
                        await btn.click()
                        await page.wait_for_timeout(300)
                        break
                except Exception:  # noqa: BLE001
                    pass

            # ── Find and click the application submit button ─────────────────
            # Walk all submit buttons; skip obvious non-apply ones (Search,
            # Subscribe, Sign in, Create alert) to avoid false positives.
            _AVOID_TEXT = {
                "search", "subscribe", "sign in", "log in",
                "create alert", "register", "close",
            }
            submitted = False
            for submit_sel in ["button[type='submit']", "input[type='submit']"]:
                btns = page.locator(submit_sel)
                count = await btns.count()
                for i in range(min(count, 10)):
                    item = btns.nth(i)
                    if not await item.is_visible():
                        continue
                    btn_text = (await item.text_content() or "").lower().strip()
                    btn_value = (await item.get_attribute("value") or "").lower().strip()
                    combined = f"{btn_text} {btn_value}"
                    if any(avoid in combined for avoid in _AVOID_TEXT):
                        logger.debug(f"Generic: skipping non-apply button '{combined[:40]}'")
                        continue
                    try:
                        await item.click(timeout=10_000)
                        submitted = True
                        break
                    except Exception:  # noqa: BLE001
                        continue
                if submitted:
                    break

            if not submitted:
                return {"status": "manual_apply", "error": "no suitable submit button found"}

            await page.wait_for_load_state("domcontentloaded", timeout=15_000)

            # ── Verify success — require a confirmation signal ────────────────
            # Without this check the handler falsely reports "applied" on
            # search results pages, login walls that didn't redirect, etc.
            final_url = page.url.lower()
            _SUCCESS_URL = ("confirm", "success", "thank", "submitted", "complete", "application-sent")
            _SUCCESS_TEXT = (
                "application submitted", "thank you for applying", "we've received",
                "your application has been", "successfully applied", "application received",
                "thanks for applying", "you have applied",
            )
            url_ok = any(s in final_url for s in _SUCCESS_URL)
            if not url_ok:
                body = (await page.text_content("body") or "").lower()[:8000]
                text_ok = any(s in body for s in _SUCCESS_TEXT)
            else:
                text_ok = False

            if not url_ok and not text_ok:
                logger.warning(
                    f"Generic: no confirmation signal for {job.get('company')} "
                    f"(URL: {page.url[:80]}) — flagging for manual apply"
                )
                return {"status": "manual_apply", "error": "no confirmation page — separate career portal, apply manually"}

            logger.info(f"Generic: applied to {job.get('company')} — {job.get('title')}")
            return {"status": "applied", "error": ""}

        except Exception as exc:  # noqa: BLE001
            logger.error(f"GenericHandler.fill error: {exc}")
            return {"status": "failed", "error": str(exc)}

    def _get_fill_instructions(self, html: str, candidate_data: dict) -> list[dict]:
        """Ask Gemini to parse the HTML and return fill instructions."""
        if self._client is None:
            return []
        try:
            prompt = self._PROMPT.format(
                candidate_data=json.dumps(candidate_data, indent=2),
                html=html[:5000],
            )
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=prompt,
            )
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"GenericHandler: Gemini instruction generation failed: {exc}")
            return []

    @staticmethod
    async def _execute_instruction(page: Any, instr: dict, resume_path: str) -> None:
        """Execute a single fill instruction on *page*."""
        selector = instr.get("selector", "")
        field_type = instr.get("type", "text")
        value = instr.get("value", "")

        try:
            el = page.locator(selector)
            if await el.count() == 0:
                return

            if field_type == "file" or value == "PLACEHOLDER_RESUME":
                await el.first.set_input_files(resume_path)
            elif field_type == "select":
                await el.first.select_option(value=value)
            else:
                # Walk through matches and fill the first visible, editable element
                # that is not a button/submit — broad CSS selectors (e.g. id*='email')
                # can accidentally match subscribe buttons or other non-form inputs.
                _NON_FILLABLE = {"submit", "button", "reset", "checkbox", "radio", "image"}
                count = await el.count()
                filled = False
                for i in range(min(count, 8)):
                    item = el.nth(i)
                    if not await item.is_visible():
                        continue
                    elem_type = (await item.get_attribute("type") or "text").lower()
                    if elem_type in _NON_FILLABLE:
                        continue
                    await item.fill(str(value))
                    filled = True
                    break
                if not filled:
                    logger.debug(f"Generic: no fillable element found for '{selector}'")
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Generic: could not execute instruction for '{selector}': {exc}")
