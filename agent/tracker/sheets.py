"""
agent/tracker/sheets.py — Google Sheets sync via gspread.

Maintains two sheets in the configured Google Spreadsheet:
  - "Applications" — one row per submitted application
  - "Stats"        — summary statistics updated each run

Credentials are loaded from the path set in GOOGLE_SHEETS_CREDENTIALS_PATH
(defaults to ``auth/google_credentials.json``).  If credentials are missing
or gspread is not installed, all methods degrade gracefully to no-ops.
"""

import os
from datetime import datetime
from typing import Any

from loguru import logger

_SHEET_HEADERS = [
    "Date Applied",
    "Company",
    "Role",
    "URL",
    "Source",
    "Match Score",
    "Status",
    "Notes",
]


class SheetsTracker:
    """Sync application data to a Google Sheets spreadsheet."""

    def __init__(self) -> None:
        """Initialise the Google Sheets client."""
        self._sheet = None
        self._gc = None
        self._sheet_id = os.getenv("GOOGLE_SHEET_ID")
        creds_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS_PATH", "auth/google_credentials.json")

        if not self._sheet_id or not os.path.exists(creds_path):
            logger.info("Google Sheets not configured — tracking to Sheets disabled.")
            return

        try:
            import gspread  # type: ignore[import]
            from google.oauth2.service_account import Credentials  # type: ignore[import]

            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
            creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
            self._gc = gspread.authorize(creds)
            logger.info("Google Sheets client initialised.")
        except ImportError:
            logger.warning("gspread not installed — Sheets tracking disabled.")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"SheetsTracker init failed: {exc}")

    def _get_or_create_sheet(self, name: str) -> Any:
        """Return worksheet *name*, creating it (with headers) if absent."""
        if self._gc is None or not self._sheet_id:
            return None
        try:
            wb = self._gc.open_by_key(self._sheet_id)
            try:
                return wb.worksheet(name)
            except Exception:  # noqa: BLE001
                ws = wb.add_worksheet(title=name, rows=1000, cols=20)
                if name == "Applications":
                    ws.append_row(_SHEET_HEADERS)
                return ws
        except Exception as exc:  # noqa: BLE001
            logger.error(f"SheetsTracker._get_or_create_sheet('{name}') failed: {exc}")
            return None

    def append_application(self, job: dict[str, Any]) -> None:
        """Append a new row for *job* to the "Applications" sheet.

        Args:
            job: Canonical job dict with tracked fields.
        """
        ws = self._get_or_create_sheet("Applications")
        if ws is None:
            return
        try:
            ws.append_row([
                datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
                job.get("company", ""),
                job.get("title", ""),
                job.get("url", ""),
                job.get("source", ""),
                job.get("match_score", ""),
                job.get("status", "applied"),
                job.get("notes", ""),
            ])
            logger.debug(f"Sheets: appended row for {job.get('company')}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"SheetsTracker.append_application failed: {exc}")

    def update_status(self, url: str, status: str) -> None:
        """Find the row matching *url* and update its Status cell.

        Args:
            url: The job application URL (used as lookup key).
            status: New status string, e.g. "interviewing", "rejected".
        """
        ws = self._get_or_create_sheet("Applications")
        if ws is None:
            return
        try:
            cell = ws.find(url)
            if cell:
                ws.update_cell(cell.row, _SHEET_HEADERS.index("Status") + 1, status)
                logger.debug(f"Sheets: updated status for {url} → {status}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"SheetsTracker.update_status failed: {exc}")

    def sync_stats(self, stats: dict[str, Any]) -> None:
        """Write aggregated stats to the "Stats" sheet.

        Args:
            stats: Dict returned by ``Database.get_stats()``.
        """
        ws = self._get_or_create_sheet("Stats")
        if ws is None:
            return
        try:
            ws.clear()
            ws.append_row(["Metric", "Value", "Updated At"])
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            ws.append_row(["Total Applied", stats.get("total_applied", 0), now])
            for platform, count in stats.get("by_platform", {}).items():
                ws.append_row([f"Platform: {platform}", count, now])
            for status, count in stats.get("by_status", {}).items():
                ws.append_row([f"Status: {status}", count, now])
            logger.debug("Sheets: stats synced")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"SheetsTracker.sync_stats failed: {exc}")
