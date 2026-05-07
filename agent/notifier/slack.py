"""
agent/notifier/slack.py — All Slack messaging via Incoming Webhook.

Messages are sent via a simple HTTP POST to the Slack Incoming Webhook
URL stored in the SLACK_WEBHOOK_URL environment variable.  If the
variable is not set, all methods silently no-op so the pipeline is not
interrupted by a missing Slack configuration.
"""

import json
import os
from datetime import datetime
from typing import Any

import urllib.request
import urllib.error

from loguru import logger


class SlackNotifier:
    """Send structured Slack notifications via Incoming Webhook."""

    def __init__(self) -> None:
        """Read the webhook URL from the environment."""
        self._webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
        if not self._webhook_url:
            logger.info("SLACK_WEBHOOK_URL not set — Slack notifications disabled.")

    # ── Public notification methods ───────────────────────────────────────

    def notify_start(self) -> None:
        """Send an agent-started notification."""
        self._post({
            "text": f"🤖 *Job Agent started* — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        })

    def notify_scrape_complete(self, count: int) -> None:
        """Send a notification with the number of new jobs found.

        Args:
            count: Number of new (unseen) jobs found in this run.
        """
        self._post({"text": f"🔍 Scraping complete — *{count}* new jobs found."})

    def notify_preview(self, jobs: list[dict[str, Any]]) -> None:
        """Send a preview list of jobs about to be applied to.

        Args:
            jobs: Shortlisted job dicts (after AI scoring).
        """
        if not jobs:
            return
        lines = ["📋 *About to apply to:*"]
        for job in jobs:
            lines.append(
                f"• {job.get('company')} — {job.get('title')} "
                f"(score: {job.get('match_score', 0):.1f}) <{job.get('url')}|link>"
            )
        lines.append("\n⏳ _Apply in 60 seconds unless aborted (Ctrl+C)_")
        self._post({"text": "\n".join(lines)})

    def notify_applied(self, job: dict[str, Any]) -> None:
        """Send a per-job success notification.

        Args:
            job: Job dict of the successfully submitted application.
        """
        self._post({
            "text": (
                f"✅ *Applied!* {job.get('company')} — {job.get('title')}\n"
                f"Score: {job.get('match_score', 0):.1f} | <{job.get('url')}|View Job>"
            )
        })

    def notify_skipped(self, job: dict[str, Any], reason: str) -> None:
        """Send a notification that a job was skipped.

        Args:
            job: Job dict.
            reason: Human-readable reason for skipping.
        """
        self._post({
            "text": (
                f"⏭ *Skipped* {job.get('company')} — {job.get('title')}\n"
                f"Reason: {reason}"
            )
        })

    def notify_failed(self, job: dict[str, Any], error: str) -> None:
        """Send a notification that an application attempt failed.

        Args:
            job: Job dict.
            error: Error message string.
        """
        self._post({
            "text": (
                f"❌ *Failed* {job.get('company')} — {job.get('title')}\n"
                f"Error: {error}"
            )
        })

    def notify_summary(self, stats: dict[str, Any]) -> None:
        """Send a run-summary notification.

        Args:
            stats: Dict returned by ``Database.get_stats()``.
        """
        by_status = stats.get("by_status", {})
        self._post({
            "text": (
                f"📊 *Run complete*\n"
                f"Total applied: {stats.get('total_applied', 0)}\n"
                f"Applied this run: {by_status.get('applied', 0)}\n"
                f"Failed: {by_status.get('failed', 0)}"
            )
        })

    # ── Internal helpers ──────────────────────────────────────────────────

    def _post(self, payload: dict) -> None:
        """POST *payload* as JSON to the Slack webhook URL.

        Silently ignores errors so a Slack outage never crashes the pipeline.

        Args:
            payload: Slack message payload dict.
        """
        if not self._webhook_url:
            return
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                if resp.status not in (200, 201):
                    logger.warning(f"Slack webhook returned status {resp.status}")
        except urllib.error.URLError as exc:
            logger.warning(f"Slack notification failed (network): {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Slack notification failed: {exc}")
