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
    "Date",             # 0  timestamp of the event (shortlisted / applied)
    "Company",          # 1
    "Role",             # 2
    "URL",              # 3  used as the unique key for upserts
    "Source",           # 4  platform name
    "Location",         # 5
    "Embedding Score",  # 6  sentence-transformers cosine similarity (0-100)
    "Gemini Score",     # 7  Gemini's own fit assessment (0-100)
    "Gemini Reasons",   # 8  semicolon-joined reasons from Gemini
    "Outcome",          # 9  shortlisted / applied / failed / dry_run
    "Notes",            # 10 error message or skip reason
    "Session ID",       # 11 pipeline run that produced this row
]

_MANUAL_HEADERS = [
    "Date",            # 0
    "Company",         # 1
    "Role",            # 2
    "URL",             # 3  unique key
    "Source",          # 4
    "Location",        # 5
    "Gemini Score",    # 6
    "Gemini Reasons",  # 7
    "Reason",          # 8  why manual apply is needed
    "Session ID",      # 9
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

    def _get_or_create_sheet(self, name: str, headers: list[str] | None = None) -> Any:
        """Return worksheet *name*, creating it (with headers) if absent."""
        if self._gc is None or not self._sheet_id:
            return None
        try:
            wb = self._gc.open_by_key(self._sheet_id)
            try:
                return wb.worksheet(name)
            except Exception:  # noqa: BLE001
                ws = wb.add_worksheet(title=name, rows=1000, cols=20)
                _hdr = headers or ({
                    "Applications": _SHEET_HEADERS,
                    "Manual Apply": _MANUAL_HEADERS,
                }.get(name))
                if _hdr:
                    ws.append_row(_hdr)
                return ws
        except Exception as exc:  # noqa: BLE001
            logger.error(f"SheetsTracker._get_or_create_sheet('{name}') failed: {exc}")
            return None

    def upsert_job(self, job: dict[str, Any], outcome: str) -> None:
        """Insert or update a job row in the "Applications" sheet.

        Uses the job URL as the unique key.  If a row already exists for
        this URL, its outcome/score fields are updated while the original
        date is preserved.  Otherwise a new row is appended.

        Args:
            job: Job dict with scraper + AI fields attached.
            outcome: Pipeline stage — "shortlisted", "applied", "failed", etc.
        """
        ws = self._get_or_create_sheet("Applications")
        if ws is None:
            return
        url = job.get("url", "")
        if not url:
            return
        reasons = job.get("match_reasons") or []
        # match_reasons may be a pre-joined string (from DB) or a list (from pipeline)
        reasons_str = reasons if isinstance(reasons, str) else "; ".join(reasons)
        # Truncate notes — failure_reason can contain full Playwright tracebacks
        notes = str(job.get("notes", "") or "")[:400]
        row_data = [
            datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
            job.get("company", "") or "(unknown)",
            job.get("title", ""),
            url,
            job.get("source", ""),
            job.get("location", ""),
            round(float(job.get("match_score") or 0), 1),   # embedding score
            round(float(job.get("gemini_score") or 0), 1),  # Gemini score
            reasons_str,
            outcome,
            notes,
            job.get("session_id", ""),
        ]
        try:
            all_values = ws.get_all_values()
            url_col = _SHEET_HEADERS.index("URL")
            existing_row_num = None
            for i, row in enumerate(all_values[1:], start=2):  # skip header row
                if len(row) > url_col and row[url_col] == url:
                    existing_row_num = i
                    break
            end_col = chr(ord("A") + len(row_data) - 1)
            if existing_row_num:
                row_data[0] = all_values[existing_row_num - 1][0]  # keep original date
                ws.update(f"A{existing_row_num}:{end_col}{existing_row_num}", [row_data])
            else:
                # Use explicit row number instead of append_row — gspread's table-detection
                # can pick the wrong anchor column and shift data sideways.
                next_row = len(all_values) + 1
                ws.update(f"A{next_row}:{end_col}{next_row}", [row_data])
            logger.debug(f"Sheets: upserted '{outcome}' for {job.get('company')}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"SheetsTracker.upsert_job failed: {exc}")

    def append_application(self, job: dict[str, Any]) -> None:
        """Append a submitted application row (delegates to upsert_job).

        Args:
            job: Canonical job dict with tracked fields.
        """
        self.upsert_job(job, job.get("status", "applied"))

    def upsert_manual_apply(self, job: dict[str, Any], reason: str) -> None:
        """Insert or update a row in the 'Manual Apply' sheet.

        Args:
            job: Canonical job dict.
            reason: Why this job needs manual application.
        """
        ws = self._get_or_create_sheet("Manual Apply")
        if ws is None:
            return
        url = job.get("url", "")
        if not url:
            return
        reasons = job.get("match_reasons") or []
        reasons_str = reasons if isinstance(reasons, str) else "; ".join(reasons)
        row_data = [
            datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
            job.get("company", "") or "(unknown)",
            job.get("title", ""),
            url,
            job.get("source", ""),
            job.get("location", ""),
            round(float(job.get("gemini_score") or 0), 1),
            reasons_str,
            reason[:400],
            job.get("session_id", ""),
        ]
        try:
            all_values = ws.get_all_values()
            # Ensure header row exists (sheet may have been created without headers)
            if not all_values or all_values[0][0] != "Date":
                ws.insert_row(_MANUAL_HEADERS, 1)
                all_values = [_MANUAL_HEADERS]
            url_col = _MANUAL_HEADERS.index("URL")
            existing_row_num = None
            for i, row in enumerate(all_values[1:], start=2):
                if len(row) > url_col and row[url_col] == url:
                    existing_row_num = i
                    break
            end_col = chr(ord("A") + len(row_data) - 1)
            if existing_row_num:
                row_data[0] = all_values[existing_row_num - 1][0]  # keep original date
                ws.update(f"A{existing_row_num}:{end_col}{existing_row_num}", [row_data])
            else:
                next_row = len(all_values) + 1
                ws.update(f"A{next_row}:{end_col}{next_row}", [row_data])
            logger.debug(f"Sheets: manual_apply logged for {job.get('company')}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"SheetsTracker.upsert_manual_apply failed: {exc}")

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
                ws.update_cell(cell.row, _SHEET_HEADERS.index("Outcome") + 1, status)
                logger.debug(f"Sheets: updated outcome for {url} → {status}")
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
