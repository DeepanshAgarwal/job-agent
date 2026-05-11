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
import re
import time
from typing import Any

from loguru import logger

from agent.ai._gemini import get_client_and_model


class Analyzer:
    """Use Gemini Flash to decide whether to apply to each job."""

    _PROMPT_TEMPLATE = """
You are an expert job application assistant helping a candidate decide whether to apply.

## Candidate Profile
Summary: {summary}
Skills: {skills}
Experience: {experience}
Seniority: {total_years} year(s) of experience — target levels: {seniority_levels}

## Candidate Preferences
- Target roles: {roles}
- Min salary: {min_lpa} LPA / ${min_usd} USD/year
- Blacklisted keywords: {blacklist}
- Must-have keywords: {must_have}

## Job Listing
Title: {title}
Company: {company}
Location: {location}
Salary/CTC: {job_salary}
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

Rules:
- SKIP if any blacklisted keyword appears in the title or description.
- SKIP if the role clearly requires a seniority level far above the candidate (e.g. 5+ years when candidate has <1 year). Entry/junior/associate roles are fine.
- SKIP only if the candidate is missing the CORE technology stack of the role — not every listed skill. Minor gaps (e.g. one library out of many) are fine; transferable/equivalent skills count.
- SKIP if the Salary/CTC field is non-empty and the value is below the candidate minimum ({min_lpa} LPA / ${min_usd} USD/year). Also skip if the salary is mentioned in the description and is clearly below the minimum. If salary is unknown/not mentioned, do NOT skip on salary grounds.
- When in doubt, APPLY — a borderline match is better than a missed opportunity.
- Set match_score based on overall stack alignment, seniority fit, and role relevance (0-100).
"""

    def __init__(self) -> None:
        """Initialise the Gemini client."""
        self._client, self._model_name = get_client_and_model()
        if self._client is None:
            logger.warning("Gemini unavailable — Analyzer will approve all jobs.")

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

        if self._client is None:
            return default

        prompt = self._PROMPT_TEMPLATE.format(
            summary=resume_dict.get("summary", ""),
            skills=", ".join(resume_dict.get("skills") or []),
            experience="; ".join(
                f"{e.get('title', '')} at {e.get('company', '')} ({e.get('duration', '')})"
                for e in (resume_dict.get("experience") or [])
            ) or "Not specified",
            total_years=resume_dict.get("total_years", 0),
            seniority_levels=", ".join(preferences.get("seniority_levels") or ["entry", "junior"]),
            roles=", ".join(preferences.get("roles") or []),
            min_lpa=preferences.get("salary", {}).get("min_lpa", 0),
            min_usd=preferences.get("salary", {}).get("min_usd_yearly", 0),
            blacklist=", ".join(preferences.get("keywords_blacklist") or []),
            must_have=", ".join(preferences.get("keywords_must_have") or []),
            title=job.get("title", ""),
            company=job.get("company", ""),
            location=job.get("location", ""),
            job_salary=job.get("salary") or "Not specified",
            description=job.get("description", "")[:3000],
        )

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self._client.models.generate_content(
                    model=self._model_name,
                    contents=prompt,
                )
                text = response.text.strip()
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                result = json.loads(text)
                result.setdefault("should_apply", True)
                result.setdefault("match_score", default["match_score"])
                result.setdefault("match_reasons", [])
                result.setdefault("skip_reason", None)
                return result
            except json.JSONDecodeError as exc:
                logger.error(f"Analyzer: Gemini returned invalid JSON: {exc}")
                break  # malformed response — no point retrying
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    match = re.search(r"retry[^\d]*(\d+)", msg, re.IGNORECASE)
                    raw_wait = int(match.group(1)) + 2 if match else 60
                    wait = min(raw_wait, 120)  # never wait more than 2 minutes
                    if raw_wait > 120:
                        logger.warning(f"Analyzer: API requested {raw_wait}s wait — capping at 120s")
                    if attempt < max_retries - 1:
                        logger.warning(f"Analyzer: rate-limited, retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    else:
                        logger.error(f"Analyzer: rate-limit exhausted after {max_retries} attempts — skipping job")
                elif "503" in msg or "UNAVAILABLE" in msg:
                    wait = 15 * (attempt + 1)  # 15s, 30s, 45s
                    if attempt < max_retries - 1:
                        logger.warning(f"Analyzer: Gemini unavailable (503), retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    else:
                        logger.error("Analyzer: Gemini unavailable after retries — skipping job")
                else:
                    logger.error(f"Analyzer.analyze failed: {exc}")
                break

        return default
