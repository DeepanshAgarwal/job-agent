"""
agent/submitter/form_filler.py — Main Playwright application controller.

Detects the ATS platform from the job URL and routes to the appropriate
platform-specific filler.  Screenshots are saved on both success and
failure for auditing purposes.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

_SCREENSHOTS_DIR = Path(__file__).parent.parent.parent / "data" / "screenshots"

# Platform URL patterns → handler module key
_PLATFORM_PATTERNS: list[tuple[str, str]] = [
    (r"greenhouse\.io", "greenhouse"),
    (r"lever\.co", "lever"),
    (r"naukri\.com", "naukri"),
    (r"instahyre\.com", "instahyre"),
]


class FormFiller:
    """Route job applications to the correct platform handler."""

    async def apply_to_job(
        self,
        job: dict[str, Any],
        profile: dict[str, Any],
        cover_letter: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Apply to *job* using the appropriate platform handler.

        Args:
            job: Canonical job dict (must contain ``url``).
            profile: Profile dict loaded from profile.yaml.
            cover_letter: Generated cover letter string.
            dry_run: If True, navigate to the form but do not submit.

        Returns:
            Dict with keys:
                status: "applied" | "dry_run" | "failed"
                error: error message string (empty on success)
                screenshot_path: path to the screenshot file
        """
        url = job.get("url", "")
        platform = self._detect_platform(url)

        _SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_company = re.sub(r"[^a-zA-Z0-9]", "_", job.get("company", "unknown"))[:30]
        screenshot_path = str(_SCREENSHOTS_DIR / f"{ts}_{safe_company}_{platform}.png")

        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ImportError:
            logger.error("playwright not installed — cannot submit applications.")
            return {"status": "failed", "error": "playwright not installed", "screenshot_path": ""}

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            try:
                handler = self._get_handler(platform)
                result = await handler.fill(page, job, profile, cover_letter, dry_run)
                await page.screenshot(path=screenshot_path)
                result["screenshot_path"] = screenshot_path
                return result
            except Exception as exc:  # noqa: BLE001
                logger.error(f"FormFiller error for {url}: {exc}")
                try:
                    await page.screenshot(path=screenshot_path)
                except Exception:  # noqa: BLE001
                    pass
                return {"status": "failed", "error": str(exc), "screenshot_path": screenshot_path}
            finally:
                await context.close()
                await browser.close()

    # ── Private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _detect_platform(url: str) -> str:
        """Identify the ATS platform from the job URL."""
        for pattern, name in _PLATFORM_PATTERNS:
            if re.search(pattern, url):
                return name
        return "generic"

    @staticmethod
    def _get_handler(platform: str) -> Any:
        """Return an initialised platform handler instance."""
        if platform == "greenhouse":
            from agent.submitter.platforms.greenhouse import GreenhouseHandler
            return GreenhouseHandler()
        if platform == "lever":
            from agent.submitter.platforms.lever import LeverHandler
            return LeverHandler()
        if platform == "naukri":
            from agent.submitter.platforms.naukri_apply import NaukriApplyHandler
            return NaukriApplyHandler()
        if platform == "instahyre":
            from agent.submitter.platforms.instahyre_apply import InstahyreApplyHandler
            return InstahyreApplyHandler()
        from agent.submitter.platforms.generic import GenericHandler
        return GenericHandler()
