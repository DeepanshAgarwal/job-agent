"""
agent/ai/_gemini.py — Shared Gemini client utilities.

Provides a single cached (client, model_name) pair so that model
discovery only ever runs once per process regardless of how many AI
classes (Analyzer, Writer, ResumeParser) are initialised.
"""

import os
from typing import Any

from loguru import logger

# Models tried in priority order — first one that responds wins.
# Free-tier RPD limits (as of May 2026) — check yours at aistudio.google.com/rate-limit:
#   gemini-3.1-flash-lite : 500 RPD  ← best for high-volume free-tier use
#   gemini-2.5-flash      :  20 RPD
#   gemini-2.5-flash-lite :  20 RPD
# NOTE: gemini-2.0-* and gemini-1.5-* are deprecated as of May 2026.
_MODEL_PRIORITY: list[str] = [
    "models/gemini-3.1-flash-lite",
    "models/gemini-2.5-flash",
    "models/gemini-2.5-flash-lite",
]

# Module-level cache: None = not yet probed, tuple = (client, model) result.
_cached: tuple[Any, str | None] | None = None


def get_client_and_model() -> tuple[Any, str | None]:
    """Return a working ``(client, model_name)`` pair.

    The first call probes ``_MODEL_PRIORITY`` in order and caches the
    result.  Subsequent calls return the cached pair instantly.

    Returns:
        ``(client, model_name)`` on success, ``(None, None)`` if Gemini
        is unavailable or no working model is found.
    """
    global _cached
    if _cached is not None:
        return _cached

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        _cached = (None, None)
        return _cached

    try:
        from google import genai  # type: ignore[import]

        client = genai.Client(api_key=api_key)
        for model in _MODEL_PRIORITY:
            try:
                client.models.generate_content(model=model, contents="ok")
                logger.info(f"Gemini: using {model}")
                _cached = (client, model)
                return _cached
            except Exception:  # noqa: BLE001
                continue

        logger.warning("No working Gemini model found for this API key.")
        _cached = (None, None)

    except ImportError:
        logger.warning("google-genai not installed — Gemini features disabled.")
        _cached = (None, None)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Gemini client init failed: {exc}")
        _cached = (None, None)

    return _cached
