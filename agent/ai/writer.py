"""
agent/ai/writer.py — Cover letter + custom Q&A generation via Gemini.

Generates tailored, concise cover letters (≤150 words) and answers to
custom application questions using Google Gemini Flash.
"""

import os
from typing import Any

from loguru import logger


class Writer:
    """Generate cover letters and custom question answers with Gemini."""

    _COVER_LETTER_PROMPT = """
You are an expert career coach writing job application cover letters.

Candidate:
- Name: {name}
- Skills: {skills}
- Experience summary: {summary}

Job:
- Title: {title}
- Company: {company}
- Key requirements (first 1000 chars of JD): {jd_snippet}

Write a cover letter with these strict rules:
1. Maximum 150 words — DO NOT exceed this.
2. Mention the company name specifically.
3. Reference 2-3 specific skills that match the job.
4. No generic buzzwords (passionate, synergy, leverage, etc.).
5. Professional but warm tone.
6. No "Dear Hiring Manager" — start directly with a strong first sentence.

Return ONLY the cover letter text, no subject line, no greeting line label.
"""

    _CUSTOM_QA_PROMPT = """
You are helping a job candidate answer a custom application question.

Candidate profile:
{profile_summary}

Job: {title} at {company}

Question: {question}

Write a concise, honest, and specific answer (2-4 sentences max).
Return only the answer text, no preamble.
"""

    def __init__(self) -> None:
        """Initialise the Gemini model."""
        self._model = None
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY not set — Writer will use placeholder text.")
            return
        try:
            import google.generativeai as genai  # type: ignore[import]

            genai.configure(api_key=api_key)
            self._model = genai.GenerativeModel("gemini-1.5-flash")
        except ImportError:
            logger.warning("google-generativeai not installed — Writer degraded.")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Writer init failed: {exc}")

    def generate_cover_letter(self, resume_dict: dict[str, Any], job: dict[str, Any]) -> str:
        """Generate a tailored cover letter for *job*.

        Args:
            resume_dict: Structured resume from ResumeParser.
            job: Canonical job dict from any scraper.

        Returns:
            Cover letter as a plain string.  Falls back to a generic
            placeholder if Gemini is unavailable.
        """
        if self._model is None:
            return self._fallback_cover_letter(resume_dict, job)

        prompt = self._COVER_LETTER_PROMPT.format(
            name=resume_dict.get("name", "The candidate"),
            skills=", ".join(resume_dict.get("skills", [])[:10]),
            summary=resume_dict.get("summary", ""),
            title=job.get("title", ""),
            company=job.get("company", ""),
            jd_snippet=job.get("description", "")[:1000],
        )

        try:
            response = self._model.generate_content(prompt)
            return response.text.strip()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Writer.generate_cover_letter failed: {exc}")
            return self._fallback_cover_letter(resume_dict, job)

    def answer_custom_question(self, question: str, profile: dict[str, Any], job: dict[str, Any]) -> str:
        """Answer a custom application form question.

        Args:
            question: The question text from the application form.
            profile: Profile dict loaded from profile.yaml.
            job: Canonical job dict.

        Returns:
            Answer as a plain string.
        """
        if self._model is None:
            return "I am excited about this opportunity and believe my experience aligns well with the role."

        personal = profile.get("personal", {})
        answers = profile.get("standard_answers", {})
        profile_summary = (
            f"Name: {personal.get('first_name')} {personal.get('last_name')}, "
            f"Location: {personal.get('location')}, "
            f"Notice period: {answers.get('notice_period')}, "
            f"Expected CTC: {answers.get('expected_ctc')}, "
            f"Willing to relocate: {answers.get('willing_to_relocate')}, "
            f"Work auth: {answers.get('work_authorization')}"
        )

        prompt = self._CUSTOM_QA_PROMPT.format(
            profile_summary=profile_summary,
            title=job.get("title", ""),
            company=job.get("company", ""),
            question=question,
        )

        try:
            response = self._model.generate_content(prompt)
            return response.text.strip()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Writer.answer_custom_question failed: {exc}")
            return "I am excited about this opportunity and believe my skills are a strong match."

    # ── Private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _fallback_cover_letter(resume_dict: dict[str, Any], job: dict[str, Any]) -> str:
        """Return a generic cover letter when AI is unavailable."""
        name = resume_dict.get("name", "I")
        skills = ", ".join(resume_dict.get("skills", [])[:3])
        company = job.get("company", "your company")
        title = job.get("title", "the role")
        return (
            f"I am writing to express my strong interest in the {title} position at {company}. "
            f"With expertise in {skills}, I am confident I can contribute meaningfully to your team. "
            f"I am eager to bring my experience to {company} and would love the opportunity to discuss "
            f"how my background aligns with your needs."
        )
