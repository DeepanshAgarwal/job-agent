"""
agent/submitter/platforms/generic.py — AI-guided fallback form filler.

When no specific platform handler matches the job URL, this handler
uses Google Gemini to analyse the page HTML and identify form fields,
then fills them intelligently.  This is a best-effort approach and may
not work perfectly on all sites.
"""

import json
import os
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
        """Initialise the Gemini model."""
        self._model = None
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return
        try:
            import google.generativeai as genai  # type: ignore[import]

            genai.configure(api_key=api_key)
            self._model = genai.GenerativeModel("gemini-1.5-flash")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"GenericHandler: Gemini init failed — {exc}")

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
            html = await page.content()

            instructions = self._get_fill_instructions(html, candidate_data)
            for instr in instructions:
                await self._execute_instruction(page, instr, resume_path)

            if dry_run:
                logger.info("Generic: dry_run=True — not submitting")
                return {"status": "dry_run", "error": ""}

            # Try to find and click the submit button
            for submit_sel in ["button[type='submit']", "input[type='submit']", "button.submit"]:
                btn = page.locator(submit_sel)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    break

            logger.info(f"Generic: applied to {job.get('company')} — {job.get('title')}")
            return {"status": "applied", "error": ""}

        except Exception as exc:  # noqa: BLE001
            logger.error(f"GenericHandler.fill error: {exc}")
            return {"status": "failed", "error": str(exc)}

    def _get_fill_instructions(self, html: str, candidate_data: dict) -> list[dict]:
        """Ask Gemini to parse the HTML and return fill instructions."""
        if self._model is None:
            return []
        try:
            prompt = self._PROMPT.format(
                candidate_data=json.dumps(candidate_data, indent=2),
                html=html[:5000],
            )
            response = self._model.generate_content(prompt)
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
                await el.first.fill(str(value))
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Generic: could not execute instruction for '{selector}': {exc}")
