from __future__ import annotations

from app.core.scoring import EmbeddingService, HybridScorer
from app.core.types import FilterCriteria, MatchResult, MatchWeights, NormalizedJob, ResumeProfile


class JobMatcher:
    def __init__(self, model_name: str, weights: MatchWeights) -> None:
        self.embedding_service = EmbeddingService(model_name)
        self.weights = weights

    def ensure_embeddings(
        self,
        resume: ResumeProfile,
        jobs: list[NormalizedJob],
    ) -> tuple[ResumeProfile, list[NormalizedJob], dict[int, list[float]]]:
        resume_embedding = resume.embedding
        if resume_embedding is None:
            resume_embedding = self.embedding_service.encode([resume.summary_text])[0]
            resume.embedding = resume_embedding

        missing_jobs = [job for job in jobs if job.embedding is None]
        if missing_jobs:
            vectors = self.embedding_service.encode([job.summary_text for job in missing_jobs])
            for job, vector in zip(missing_jobs, vectors, strict=False):
                job.embedding = vector

        job_embeddings = {job.id: job.embedding for job in jobs if job.id is not None and job.embedding is not None}
        return resume, jobs, job_embeddings

    def match(self, resume: ResumeProfile, jobs: list[NormalizedJob], filters: FilterCriteria) -> list[MatchResult]:
        filtered_jobs = [job for job in jobs if self._job_matches_filters(job, filters)]
        if not filtered_jobs or resume.embedding is None:
            return []

        scorer = HybridScorer(self.weights)
        results: list[MatchResult] = []
        resume_skill_set = set(resume.skills)
        for job in filtered_jobs:
            if job.embedding is None or job.id is None:
                continue
            final_score, embedding_score, skill_score, experience_score = scorer.score(
                resume_embedding=resume.embedding,
                job_embedding=job.embedding,
                resume_skills=resume.skills,
                job_required_skills=job.required_skills,
                job_preferred_skills=job.preferred_skills,
                resume_experience_years=resume.experience_years,
                job_experience_years=job.experience_years,
            )
            target_skills = job.required_skills or job.skills
            matched_skills = sorted([skill for skill in target_skills if skill in resume_skill_set], key=str.casefold)
            missing_skills = sorted([skill for skill in target_skills if skill not in resume_skill_set], key=str.casefold)
            reasons = self._build_reasons(job, embedding_score, skill_score, experience_score, matched_skills, missing_skills)
            results.append(
                MatchResult(
                    job_id=job.id,
                    score=final_score,
                    embedding_score=embedding_score,
                    skill_score=skill_score,
                    experience_score=experience_score,
                    matched_skills=matched_skills,
                    missing_skills=missing_skills,
                    reasons=reasons,
                    job=job,
                )
            )
        return sorted(results, key=lambda result: result.score, reverse=True)

    @staticmethod
    def _job_matches_filters(job: NormalizedJob, filters: FilterCriteria) -> bool:
        if filters.source_ids and job.source_id not in filters.source_ids:
            return False
        if filters.location_query:
            if filters.location_query.casefold() not in f"{job.location} {job.description}".casefold():
                return False
        if filters.remote_mode != "any" and job.remote_mode != filters.remote_mode:
            return False
        if filters.job_type != "any" and (job.job_type or "unknown") != filters.job_type:
            return False
        if filters.clearance_terms:
            job_clearance = {term.casefold() for term in job.clearance_terms}
            requested = {term.casefold() for term in filters.clearance_terms}
            if not requested.issubset(job_clearance):
                return False
        return True

    @staticmethod
    def _build_reasons(
        job: NormalizedJob,
        embedding_score: float,
        skill_score: float,
        experience_score: float,
        matched_skills: list[str],
        missing_skills: list[str],
    ) -> list[str]:
        reasons = [
            f"Semantic fit scored {embedding_score * 100:.0f}% based on the resume summary and job description.",
            f"Skill overlap scored {skill_score * 100:.0f}% with {len(matched_skills)} matched skill(s).",
            f"Experience alignment scored {experience_score * 100:.0f}% against the stated years requirement.",
        ]
        if matched_skills:
            reasons.append(f"Matched skills: {', '.join(matched_skills[:8])}.")
        if missing_skills:
            reasons.append(f"Missing skills: {', '.join(missing_skills[:8])}.")
        elif job.required_skills:
            reasons.append("No obvious required-skill gaps were detected from the extracted posting text.")
        return reasons
