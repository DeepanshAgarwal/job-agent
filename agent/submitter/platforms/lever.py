"""
agent/submitter/platforms/lever.py — Lever ATS form filler.

Lever is another popular ATS with a consistent form layout.
This handler fills name, email, phone, LinkedIn, resume, and cover
letter fields.

TODO: Verify selectors against live Lever applications before first run.
"""

from typing import Any

from loguru import logger


class LeverHandler:
    """Fill and submit a Lever ATS application form."""

    # TODO: Verify these selectors on live Lever job applications
    _SELECTORS = {
        "name": "input[name='name']",
        "email": "input[name='email']",
        "phone": "input[name='phone']",
        "org": "input[name='org']",  # current company
        "urls_linkedin": "input[data-qa='urls-linkedin']",
        "resume_upload": "input[data-qa='resume-upload-input']",
        "cover_letter": "textarea[data-qa='additional-information']",
        "submit_btn": "button[data-qa='btn-submit']",
    }

    async def fill(
        self,
        page: Any,
        job: dict[str, Any],
        profile: dict[str, Any],
        cover_letter: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Navigate to the Lever form and fill all standard fields.

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
        full_name = f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip()

        try:
            await page.goto(job["url"], wait_until="domcontentloaded", timeout=30_000)

            await self._fill_if_visible(page, self._SELECTORS["name"], full_name)
            await self._fill_if_visible(page, self._SELECTORS["email"], personal.get("email", ""))
            await self._fill_if_visible(page, self._SELECTORS["phone"], personal.get("phone", ""))
            await self._fill_if_visible(page, self._SELECTORS["urls_linkedin"], links.get("linkedin", ""))
            await self._fill_if_visible(page, self._SELECTORS["cover_letter"], cover_letter)

            resume_input = page.locator(self._SELECTORS["resume_upload"])
            if await resume_input.count() > 0:
                await resume_input.set_input_files(resume_path)

            if dry_run:
                logger.info("Lever: dry_run=True — form filled but not submitted")
                return {"status": "dry_run", "error": ""}

            await page.locator(self._SELECTORS["submit_btn"]).click()
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            logger.info(f"Lever: applied to {job.get('company')} — {job.get('title')}")
            return {"status": "applied", "error": ""}

        except Exception as exc:  # noqa: BLE001
            logger.error(f"LeverHandler.fill error: {exc}")
            return {"status": "failed", "error": str(exc)}

    @staticmethod
    async def _fill_if_visible(page: Any, selector: str, value: str) -> None:
        """Fill *selector* with *value* only if the element is visible."""
        try:
            el = page.locator(selector)
            if await el.count() > 0 and await el.first.is_visible():
                await el.first.fill(value)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Lever: could not fill '{selector}': {exc}")
