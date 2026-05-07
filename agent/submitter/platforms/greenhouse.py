"""
agent/submitter/platforms/greenhouse.py — Greenhouse ATS form filler.

Greenhouse is a widely used ATS with a predictable HTML structure.
This handler fills the standard Greenhouse application form fields
(name, email, phone, resume upload, cover letter) and submits.

TODO: Verify selectors against live Greenhouse forms before first run.
"""

from typing import Any

from loguru import logger


class GreenhouseHandler:
    """Fill and submit a Greenhouse ATS application form."""

    # TODO: Verify these selectors on live Greenhouse job applications
    _SELECTORS = {
        "first_name": "#first_name",
        "last_name": "#last_name",
        "email": "#email",
        "phone": "#phone",
        "resume_upload": "input[name='resume']",
        "cover_letter_area": "#cover_letter",
        "linkedin_url": "#job_application_answers_attributes_0_text_value",
        "submit_btn": "input[type='submit']",
    }

    async def fill(
        self,
        page: Any,
        job: dict[str, Any],
        profile: dict[str, Any],
        cover_letter: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Navigate to the Greenhouse form and fill all fields.

        Args:
            page: A Playwright ``Page`` instance.
            job: Canonical job dict.
            profile: Profile dict loaded from profile.yaml.
            cover_letter: Generated cover letter string.
            dry_run: If True, fill but do not click submit.

        Returns:
            Result dict with ``status`` and ``error`` keys.
        """
        personal = profile.get("personal", {})
        links = profile.get("links", {})
        resume_path = profile.get("resume", {}).get("path", "config/resume.pdf")

        try:
            await page.goto(job["url"], wait_until="domcontentloaded", timeout=30_000)

            await self._fill_if_visible(page, self._SELECTORS["first_name"], personal.get("first_name", ""))
            await self._fill_if_visible(page, self._SELECTORS["last_name"], personal.get("last_name", ""))
            await self._fill_if_visible(page, self._SELECTORS["email"], personal.get("email", ""))
            await self._fill_if_visible(page, self._SELECTORS["phone"], personal.get("phone", ""))
            await self._fill_if_visible(page, self._SELECTORS["linkedin_url"], links.get("linkedin", ""))
            await self._fill_if_visible(page, self._SELECTORS["cover_letter_area"], cover_letter)

            # Upload resume
            resume_input = page.locator(self._SELECTORS["resume_upload"])
            if await resume_input.count() > 0:
                await resume_input.set_input_files(resume_path)

            if dry_run:
                logger.info("Greenhouse: dry_run=True — form filled but not submitted")
                return {"status": "dry_run", "error": ""}

            await page.locator(self._SELECTORS["submit_btn"]).click()
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            logger.info(f"Greenhouse: applied to {job.get('company')} — {job.get('title')}")
            return {"status": "applied", "error": ""}

        except Exception as exc:  # noqa: BLE001
            logger.error(f"GreenhouseHandler.fill error: {exc}")
            return {"status": "failed", "error": str(exc)}

    @staticmethod
    async def _fill_if_visible(page: Any, selector: str, value: str) -> None:
        """Fill *selector* with *value* only if the element is visible."""
        try:
            el = page.locator(selector)
            if await el.count() > 0 and await el.first.is_visible():
                await el.first.fill(value)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Greenhouse: could not fill '{selector}': {exc}")
