"""
agent/ai/matcher.py — Sentence-transformer embedding-based job scoring.

Loads the ``all-MiniLM-L6-v2`` model once at construction time, then
exposes ``score_job`` for single-job scoring and ``batch_score`` for
efficient batch scoring of many jobs against the same resume text.
"""

from typing import Any

from loguru import logger


class Matcher:
    """Score job relevance using sentence-transformers cosine similarity."""

    # all-mpnet-base-v2: best overall quality sentence-transformer model.
    # ~420MB, ~30-60s for 1000 jobs on CPU — acceptable for a background pipeline.
    _MODEL_NAME = "all-mpnet-base-v2"

    def __init__(self) -> None:
        """Load the sentence-transformers model (cached on first call)."""
        self._model = None
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]

            self._model = SentenceTransformer(self._MODEL_NAME)
            logger.info(f"Matcher: loaded model '{self._MODEL_NAME}'")
        except ImportError:
            logger.warning("sentence-transformers not installed — matcher will return 0 scores.")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Matcher: failed to load model — {exc}")

    def score_job(self, resume_text: str, job_description: str) -> float:
        """Return a 0-100 similarity score between resume and job description.

        Args:
            resume_text: Full plain-text content of the resume.
            job_description: Full job description text.

        Returns:
            Float between 0 and 100.  Returns 0 if model unavailable.
        """
        if self._model is None or not resume_text or not job_description:
            return 0.0

        try:
            from sentence_transformers import util  # type: ignore[import]

            embeddings = self._model.encode(
                [resume_text[:2000], job_description[:2000]], convert_to_tensor=True
            )
            cosine = float(util.cos_sim(embeddings[0], embeddings[1]))
            # Clamp to [0, 1] and convert to 0-100
            score = max(0.0, min(1.0, cosine)) * 100.0
            return round(score, 2)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Matcher.score_job failed: {exc}")
            return 0.0

    def batch_score(self, resume_text: str, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Score all jobs against *resume_text* and attach ``match_score``.

        This is more efficient than calling ``score_job`` in a loop because
        it encodes all job descriptions in a single batch forward pass.

        Args:
            resume_text: Full plain-text content of the resume.
            jobs: List of job dicts (must contain a ``description`` key).

        Returns:
            The same list of job dicts, each with ``match_score`` attached.
        """
        if self._model is None:
            for job in jobs:
                job.setdefault("match_score", 0.0)
            return jobs

        if not jobs:
            return jobs

        try:
            from sentence_transformers import util  # type: ignore[import]

            resume_emb = self._model.encode(resume_text[:2000], convert_to_tensor=True)
            descriptions = [j.get("description", "")[:2000] for j in jobs]
            job_embs = self._model.encode(descriptions, convert_to_tensor=True, batch_size=32)
            scores = util.cos_sim(resume_emb, job_embs)[0].tolist()

            for job, score in zip(jobs, scores):
                job["match_score"] = round(max(0.0, min(1.0, float(score))) * 100.0, 2)

        except Exception as exc:  # noqa: BLE001
            logger.error(f"Matcher.batch_score failed: {exc}")
            for job in jobs:
                job.setdefault("match_score", 0.0)

        return jobs
