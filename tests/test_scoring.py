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
