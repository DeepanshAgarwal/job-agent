"""
agent/ai/resume_parser.py — PDF → structured dict using PyMuPDF + Gemini.

Extracts text from a PDF resume and uses Google Gemini to parse it into
a structured dictionary containing: summary, skills, experience,
education, and total years of experience.  The parsed result is cached
in a JSON sidecar file next to the PDF so it is only re-parsed when the
PDF changes.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger


class ResumeParser:
    """Parse a PDF resume into a structured dict using PyMuPDF + Gemini."""

    _CACHE_SUFFIX = ".parsed_cache.json"

    def parse(self, pdf_path: str) -> dict[str, Any]:
        """Parse *pdf_path* and return a structured resume dict.

        The result is cached alongside the PDF and reused unless the PDF
        changes (detected via MD5 hash).

        Args:
            pdf_path: Path to the resume PDF file.

        Returns:
            Dict with keys: raw_text, summary, skills, experience,
            education, total_years.  Returns a minimal dict on failure.
        """
        path = Path(pdf_path)
        if not path.exists():
            logger.warning(f"Resume not found at '{pdf_path}'. Using empty profile.")
            return {"raw_text": "", "skills": [], "experience": [], "education": [], "total_years": 0}

        pdf_hash = self._md5(path)
        cache_path = path.with_suffix(self._CACHE_SUFFIX)
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                if cached.get("_hash") == pdf_hash:
                    logger.info("Resume cache hit — skipping re-parse.")
                    return cached
            except Exception:  # noqa: BLE001
                pass

        raw_text = self._extract_text(path)
        structured = self._parse_with_gemini(raw_text)
        structured["raw_text"] = raw_text
        structured["_hash"] = pdf_hash

        try:
            cache_path.write_text(json.dumps(structured, indent=2))
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Failed to write resume cache: {exc}")

        return structured

    # ── Private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _md5(path: Path) -> str:
        """Return the MD5 hex digest of *path*."""
        return hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()

    @staticmethod
    def _extract_text(path: Path) -> str:
        """Extract all text from a PDF using PyMuPDF."""
        try:
            import fitz  # type: ignore[import]  # PyMuPDF

            doc = fitz.open(str(path))
            pages = [page.get_text() for page in doc]
            doc.close()
            return "\n".join(pages)
        except ImportError:
            logger.warning("PyMuPDF (fitz) not installed — returning empty resume text.")
            return ""
        except Exception as exc:  # noqa: BLE001
            logger.error(f"PDF text extraction failed: {exc}")
            return ""

    @staticmethod
    def _parse_with_gemini(raw_text: str) -> dict[str, Any]:
        """Use Gemini Flash to parse raw resume text into a structured dict."""
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key or not raw_text.strip():
            return {"summary": "", "skills": [], "experience": [], "education": [], "total_years": 0}

        prompt = f"""
You are a resume parser. Extract structured information from the resume text below.
Return ONLY valid JSON with these keys:
- summary: (str) 2-3 sentence professional summary
- skills: (list of str) technical skills
- experience: (list of dict) each with keys: company, title, duration, description
- education: (list of dict) each with keys: institution, degree, year
- total_years: (int) total years of professional experience

Resume:
{raw_text[:8000]}

Return only the JSON object, no markdown, no explanation.
"""
        try:
            import google.generativeai as genai  # type: ignore[import]

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            text = response.text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except ImportError:
            logger.warning("google-generativeai not installed — skipping Gemini resume parse.")
        except json.JSONDecodeError as exc:
            logger.error(f"Gemini returned invalid JSON: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Gemini resume parse failed: {exc}")

        return {"summary": "", "skills": [], "experience": [], "education": [], "total_years": 0}
