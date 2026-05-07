"""
agent/ai/analyzer.py — Gemini-powered apply/skip decision engine.

Given a structured resume dict, a job dict, and the user preferences,
asks Gemini Flash to make a structured JSON decision:
  - should_apply (bool)
  - match_score (float 0-100, Gemini's own assessment)
  - match_reasons (list[str])
  - skip_reason (str | null)
"""

import json
import os
from typing import Any

from loguru import logger


class Analyzer:
    """Use Gemini Flash to decide whether to apply to each job."""

    _PROMPT_TEMPLATE = """
You are an expert job application assistant helping a candidate decide whether to apply.

## Candidate Summary
{summary}

## Candidate Skills
{skills}

## Candidate Preferences
- Target roles: {roles}
- Min salary: {min_lpa} LPA / ${min_usd} USD/year
- Blacklisted keywords: {blacklist}
- Must-have keywords: {must_have}

## Job Listing
Title: {title}
Company: {company}
Location: {location}
Description:
{description}

## Task
Analyse the fit and return ONLY valid JSON (no markdown) with:
{{
  "should_apply": true/false,
  "match_score": <float 0-100>,
  "match_reasons": ["reason1", "reason2"],
  "skip_reason": null or "reason string"
}}

Be strict: skip if company is blacklisted, required skills are missing,
or salary is likely below the minimum.
"""

    def __init__(self) -> None:
        """Initialise the Gemini model (lazy import)."""
        self._model = None
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY not set — Analyzer will approve all jobs.")
            return
        try:
            import google.generativeai as genai  # type: ignore[import]

            genai.configure(api_key=api_key)
            self._model = genai.GenerativeModel("gemini-1.5-flash")
        except ImportError:
            logger.warning("google-generativeai not installed — Analyzer will approve all jobs.")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Analyzer init failed: {exc}")

    def analyze(self, resume_dict: dict[str, Any], job: dict[str, Any], preferences: dict) -> dict[str, Any]:
        """Return a decision dict for the given job.

        Args:
            resume_dict: Structured resume parsed by ResumeParser.
            job: Canonical job dict from any scraper.
            preferences: Loaded preferences.yaml dict.

        Returns:
            Dict with keys: should_apply, match_score, match_reasons, skip_reason.
            Defaults to ``should_apply=True`` if Gemini is unavailable.
        """
        default = {
            "should_apply": True,
            "match_score": job.get("match_score", 50.0),
            "match_reasons": ["AI analyzer unavailable — defaulting to apply"],
            "skip_reason": None,
        }

        if self._model is None:
            return default

        prompt = self._PROMPT_TEMPLATE.format(
            summary=resume_dict.get("summary", ""),
            skills=", ".join(resume_dict.get("skills", [])),
            roles=", ".join(preferences.get("roles", [])),
            min_lpa=preferences.get("salary", {}).get("min_lpa", 0),
            min_usd=preferences.get("salary", {}).get("min_usd_yearly", 0),
            blacklist=", ".join(preferences.get("keywords_blacklist", [])),
            must_have=", ".join(preferences.get("keywords_must_have", [])),
            title=job.get("title", ""),
            company=job.get("company", ""),
            location=job.get("location", ""),
            description=job.get("description", "")[:3000],
        )

        try:
            response = self._model.generate_content(prompt)
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            result = json.loads(text)
            # Ensure required keys are present
            result.setdefault("should_apply", True)
            result.setdefault("match_score", default["match_score"])
            result.setdefault("match_reasons", [])
            result.setdefault("skip_reason", None)
            return result
        except json.JSONDecodeError as exc:
            logger.error(f"Analyzer: Gemini returned invalid JSON: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Analyzer.analyze failed: {exc}")

        return default
