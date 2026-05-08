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
# Free-tier RPD limits (as of 2025):
#   gemini-2.0-flash-lite : 1500 RPD, 30 RPM
#   gemini-2.0-flash      : 1500 RPD, 15 RPM
#   gemini-1.5-flash-8b   : 1500 RPD, 15 RPM
#   gemini-1.5-flash      : 1500 RPD, 15 RPM
#   gemini-2.5-flash      :   20 RPD, 10 RPM  ← last resort only
_MODEL_PRIORITY: list[str] = [
    "models/gemini-2.0-flash-lite",
    "models/gemini-2.0-flash",
    "models/gemini-1.5-flash-8b",
    "models/gemini-1.5-flash",
    "models/gemini-2.5-flash",
    "models/gemini-flash-lite-latest",
    "models/gemini-flash-latest",
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
