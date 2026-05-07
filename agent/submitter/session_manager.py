"""
agent/submitter/session_manager.py — Save/load Playwright browser sessions.

Browser login state (cookies + localStorage) is persisted to
``auth/{platform}_session.json`` so the agent can reuse authenticated
sessions across runs without prompting for login every time.
"""

import json
from pathlib import Path
from typing import Any

from loguru import logger

_AUTH_DIR = Path(__file__).parent.parent.parent / "auth"


class SessionManager:
    """Persist and restore Playwright browser context sessions."""

    def __init__(self, auth_dir: Path | None = None) -> None:
        """Initialise the session manager.

        Args:
            auth_dir: Directory where session JSON files are stored.
                      Defaults to ``<repo_root>/auth/``.
        """
        self._auth_dir = auth_dir or _AUTH_DIR
        self._auth_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, platform: str) -> Path:
        """Return the path to the session file for *platform*."""
        return self._auth_dir / f"{platform}_session.json"

    async def save_session(self, platform: str, context: Any) -> None:
        """Persist the current browser context state to disk.

        Args:
            platform: Short platform name, e.g. ``"naukri"``.
            context: A Playwright ``BrowserContext`` instance.
        """
        try:
            storage_state = await context.storage_state()
            path = self._session_path(platform)
            path.write_text(json.dumps(storage_state, indent=2))
            logger.info(f"Session saved for '{platform}' at {path}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to save session for '{platform}': {exc}")

    async def load_session(self, platform: str, browser: Any) -> Any:
        """Load a saved session and return a new BrowserContext.

        If no session file exists for *platform*, returns a fresh context
        without any cookies.

        Args:
            platform: Short platform name, e.g. ``"naukri"``.
            browser: A Playwright ``Browser`` instance.

        Returns:
            A ``BrowserContext`` (with or without saved session).
        """
        path = self._session_path(platform)
        if path.exists():
            try:
                logger.info(f"Loading saved session for '{platform}' from {path}")
                return await browser.new_context(storage_state=str(path))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Failed to load session for '{platform}': {exc} — using fresh context")
        else:
            logger.info(f"No saved session for '{platform}' — using fresh context (login may be needed)")
        return await browser.new_context()

    def has_session(self, platform: str) -> bool:
        """Return True if a valid session file exists for *platform*."""
        return self._session_path(platform).exists()

    def delete_session(self, platform: str) -> None:
        """Delete the saved session file for *platform*."""
        path = self._session_path(platform)
        if path.exists():
            path.unlink()
            logger.info(f"Session deleted for '{platform}'")
