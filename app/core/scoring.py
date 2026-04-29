from __future__ import annotations

import contextlib
import io
import logging
import os
import threading

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from huggingface_hub.utils import disable_progress_bars
from sentence_transformers import SentenceTransformer

from app.core.types import MatchWeights
from app.utils.text import cosine_similarity

logger = logging.getLogger(__name__)


class EmbeddingService:
    _model_cache: dict[str, SentenceTransformer] = {}
    _lock = threading.Lock()

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model: SentenceTransformer | None = None

    def _get_model(self) -> SentenceTransformer:
        if self._model is not None:
            return self._model

        with self._lock:
            cached = self._model_cache.get(self.model_name)
            if cached is None:
                logger.info("Loading embedding model %s", self.model_name)
                try:
                    disable_progress_bars()
                except Exception:
                    logger.debug("Unable to disable Hugging Face progress bars cleanly.", exc_info=True)
                cached = self._run_quietly(lambda: SentenceTransformer(self.model_name))
                self._model_cache[self.model_name] = cached
            self._model = cached
            return cached

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        embeddings = self._run_quietly(
            lambda: model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        )
        return [embedding.tolist() for embedding in embeddings]

    @staticmethod
    def _run_quietly(loader):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                return loader()
        except OSError as exc:
            if getattr(exc, "errno", None) == 22:
                raise RuntimeError(
                    "Local embedding model initialization failed while writing console progress output."
                ) from exc
            raise


class HybridScorer:
    def __init__(self, weights: MatchWeights) -> None:
        self.weights = weights.normalized()

    def score(
        self,
        *,
        resume_embedding: list[float],
        job_embedding: list[float],
        resume_skills: list[str],
        job_required_skills: list[str],
        job_preferred_skills: list[str],
        resume_experience_years: float,
        job_experience_years: float | None,
    ) -> tuple[float, float, float, float]:
        embedding_score = cosine_similarity(resume_embedding, job_embedding)
        skill_score = self._skill_overlap(resume_skills, job_required_skills, job_preferred_skills)
        experience_score = self._experience_alignment(resume_experience_years, job_experience_years)
        final_score = (
            embedding_score * self.weights.embedding
            + skill_score * self.weights.skill
            + experience_score * self.weights.experience
        )
        return final_score, embedding_score, skill_score, experience_score

    @staticmethod
    def _skill_overlap(resume_skills: list[str], required_skills: list[str], preferred_skills: list[str]) -> float:
        resume_set = {skill.casefold() for skill in resume_skills}
        weighted_skills: list[tuple[str, float]] = []
        weighted_skills.extend((skill, 1.0) for skill in required_skills)
        weighted_skills.extend((skill, 0.55) for skill in preferred_skills if skill not in required_skills)
        if not weighted_skills:
            return 0.5 if resume_set else 0.0
        matched = sum(weight for skill, weight in weighted_skills if skill.casefold() in resume_set)
        total = sum(weight for _, weight in weighted_skills)
        return matched / total if total else 0.0

    @staticmethod
    def _experience_alignment(resume_years: float, required_years: float | None) -> float:
        if required_years in (None, 0):
            return 1.0
        if resume_years <= 0:
            return 0.0
        return min(resume_years / required_years, 1.0)
