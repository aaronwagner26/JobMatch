from app.core.scoring import EmbeddingService
from app.core.scoring import HybridScorer
from app.core.types import MatchWeights


def test_hybrid_scorer_weights_signal_sources() -> None:
    scorer = HybridScorer(MatchWeights(embedding=0.6, skill=0.3, experience=0.1))
    final_score, embedding_score, skill_score, experience_score = scorer.score(
        resume_embedding=[1.0, 0.0],
        job_embedding=[1.0, 0.0],
        resume_skills=["Python", "AWS", "Docker"],
        job_required_skills=["Python", "Docker"],
        job_preferred_skills=["Kubernetes"],
        resume_experience_years=6.0,
        job_experience_years=5.0,
    )

    assert round(embedding_score, 4) == 1.0
    assert skill_score > 0.7
    assert experience_score == 1.0
    assert final_score > 0.9


def test_embedding_service_reuses_loaded_model(monkeypatch) -> None:
    class FakeVector:
        def __init__(self, values) -> None:
            self.values = values

        def tolist(self):
            return self.values

    class FakeModel:
        def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):  # noqa: ANN001
            return [FakeVector([float(index)]) for index, _ in enumerate(texts, start=1)]

    loaded_models: list[str] = []

    def fake_loader(model_name: str):  # noqa: ANN001
        loaded_models.append(model_name)
        return FakeModel()

    monkeypatch.setattr("app.core.scoring.SentenceTransformer", fake_loader)
    monkeypatch.setattr("app.core.scoring.disable_progress_bars", lambda *args, **kwargs: None)
    EmbeddingService._model_cache.clear()

    first = EmbeddingService("test-model")
    second = EmbeddingService("test-model")

    assert first.encode(["resume"]) == [[1.0]]
    assert second.encode(["job"]) == [[1.0]]
    assert loaded_models == ["test-model"]
