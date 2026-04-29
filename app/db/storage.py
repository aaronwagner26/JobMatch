from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import create_engine, delete, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.types import JobSourceConfig, NormalizedJob, ResumeProfile
from app.db.models import AppSettingRecord, Base, JobRecord, ResumeRecord, ScanRecord, SourceRecord
from app.utils.config import DB_PATH, DEFAULT_SETTINGS, DEFAULT_SOURCE_MAX_PAGES, DEFAULT_SOURCE_REQUEST_DELAY_MS, ensure_directories


class Storage:
    def __init__(self, database_url: str | None = None) -> None:
        ensure_directories()
        self.database_url = database_url or f"sqlite:///{DB_PATH.as_posix()}"
        self.engine = create_engine(self.database_url, future=True)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def init_db(self) -> None:
        Base.metadata.create_all(self.engine)
        self._migrate_db()
        for key, value in DEFAULT_SETTINGS.items():
            self.set_setting(key, value)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_settings(self) -> dict:
        values = dict(DEFAULT_SETTINGS)
        with self.session() as session:
            records = session.scalars(select(AppSettingRecord)).all()
            for record in records:
                values[record.key] = record.value_json
        return values

    def get_setting(self, key: str, default=None):
        with self.session() as session:
            record = session.get(AppSettingRecord, key)
            if record is None:
                return DEFAULT_SETTINGS.get(key, default)
            return record.value_json

    def set_setting(self, key: str, value) -> None:
        with self.session() as session:
            record = session.get(AppSettingRecord, key)
            if record is None:
                record = AppSettingRecord(key=key, value_json=value)
                session.add(record)
            else:
                record.value_json = value

    def update_settings(self, values: dict) -> None:
        with self.session() as session:
            for key, value in values.items():
                record = session.get(AppSettingRecord, key)
                if record is None:
                    record = AppSettingRecord(key=key, value_json=value)
                    session.add(record)
                else:
                    record.value_json = value

    def save_resume(self, resume: ResumeProfile) -> ResumeProfile:
        with self.session() as session:
            session.query(ResumeRecord).update({ResumeRecord.is_active: False})
            record = ResumeRecord(
                filename=resume.filename,
                file_path=resume.file_path,
                file_hash=resume.file_hash,
                raw_text=resume.raw_text,
                summary_text=resume.summary_text,
                skills=resume.skills,
                tools=resume.tools,
                experience_years=resume.experience_years,
                experience_spans=resume.experience_spans,
                sections=resume.sections,
                embedding=resume.embedding,
                is_active=True,
            )
            session.add(record)
            session.flush()
            return self._resume_from_record(record)

    def get_active_resume(self) -> ResumeProfile | None:
        with self.session() as session:
            record = session.scalar(select(ResumeRecord).where(ResumeRecord.is_active.is_(True)).order_by(ResumeRecord.updated_at.desc()))
            return self._resume_from_record(record) if record else None

    def list_resumes(self) -> list[ResumeProfile]:
        with self.session() as session:
            records = session.scalars(select(ResumeRecord).order_by(ResumeRecord.updated_at.desc())).all()
            return [self._resume_from_record(record) for record in records]

    def save_resume_embedding(self, resume_id: int, embedding: list[float]) -> None:
        with self.session() as session:
            record = session.get(ResumeRecord, resume_id)
            if record:
                record.embedding = embedding

    def list_sources(self) -> list[JobSourceConfig]:
        with self.session() as session:
            records = session.scalars(select(SourceRecord).order_by(SourceRecord.enabled.desc(), SourceRecord.name.asc())).all()
            return [self._source_from_record(record) for record in records]

    def get_source(self, source_id: int) -> JobSourceConfig | None:
        with self.session() as session:
            record = session.get(SourceRecord, source_id)
            return self._source_from_record(record) if record else None

    def upsert_source(self, payload: JobSourceConfig) -> JobSourceConfig:
        with self.session() as session:
            record = session.get(SourceRecord, payload.id) if payload.id else None
            if record is None:
                record = SourceRecord(
                    name=payload.name,
                    source_type=payload.source_type,
                    url=payload.url,
                    identifier=payload.identifier,
                    enabled=payload.enabled,
                    use_playwright=payload.use_playwright,
                    use_browser_profile=payload.use_browser_profile,
                    refresh_minutes=payload.refresh_minutes,
                    max_pages=payload.max_pages,
                    request_delay_ms=payload.request_delay_ms,
                    notes=payload.notes,
                    headers=payload.headers,
                )
                session.add(record)
            else:
                record.name = payload.name
                record.source_type = payload.source_type
                record.url = payload.url
                record.identifier = payload.identifier
                record.enabled = payload.enabled
                record.use_playwright = payload.use_playwright
                record.use_browser_profile = payload.use_browser_profile
                record.refresh_minutes = payload.refresh_minutes
                record.max_pages = payload.max_pages
                record.request_delay_ms = payload.request_delay_ms
                record.notes = payload.notes
                record.headers = payload.headers
            session.flush()
            return self._source_from_record(record)

    def delete_source(self, source_id: int) -> None:
        with self.session() as session:
            record = session.get(SourceRecord, source_id)
            if record:
                session.delete(record)

    def begin_scan(self, source_id: int | None) -> int:
        with self.session() as session:
            record = ScanRecord(source_id=source_id, status="running")
            session.add(record)
            session.flush()
            return record.id

    def finish_scan(
        self,
        scan_id: int,
        *,
        status: str,
        jobs_found: int = 0,
        jobs_created: int = 0,
        jobs_updated: int = 0,
        jobs_unchanged: int = 0,
        jobs_deactivated: int = 0,
        error_text: str | None = None,
    ) -> None:
        with self.session() as session:
            record = session.get(ScanRecord, scan_id)
            if not record:
                return
            record.status = status
            record.jobs_found = jobs_found
            record.jobs_created = jobs_created
            record.jobs_updated = jobs_updated
            record.jobs_unchanged = jobs_unchanged
            record.jobs_deactivated = jobs_deactivated
            record.error_text = error_text
            record.finished_at = datetime.now(UTC)

    def list_scans(self, limit: int = 25) -> list[dict]:
        with self.session() as session:
            rows = session.execute(
                select(ScanRecord, SourceRecord.name)
                .outerjoin(SourceRecord, ScanRecord.source_id == SourceRecord.id)
                .order_by(ScanRecord.started_at.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": record.id,
                    "source_id": record.source_id,
                    "source_name": source_name or (f"Source #{record.source_id}" if record.source_id else "Unknown source"),
                    "started_at": record.started_at,
                    "finished_at": record.finished_at,
                    "status": record.status,
                    "jobs_found": record.jobs_found,
                    "jobs_created": record.jobs_created,
                    "jobs_updated": record.jobs_updated,
                    "jobs_unchanged": record.jobs_unchanged,
                    "jobs_deactivated": record.jobs_deactivated,
                    "error_text": record.error_text,
                }
                for record, source_name in rows
            ]

    def update_source_scan_state(
        self,
        source_id: int,
        *,
        status: str,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        with self.session() as session:
            record = session.get(SourceRecord, source_id)
            if record is None:
                return
            record.last_scan_at = datetime.now(UTC)
            record.last_status = status
            record.etag = etag or record.etag
            record.last_modified = last_modified or record.last_modified

    def get_source_job_index(self, source_id: int) -> dict[str, dict]:
        with self.session() as session:
            records = session.scalars(select(JobRecord).where(JobRecord.source_id == source_id)).all()
            index: dict[str, dict] = {}
            for record in records:
                metadata = dict(record.metadata_json or {})
                index[record.external_id] = {
                    "content_hash": record.content_hash,
                    "listing_hash": metadata.get("listing_hash"),
                    "canonical_url": metadata.get("canonical_url") or record.url,
                    "description": record.description,
                    "employment_text": record.employment_text,
                    "active": record.active,
                    "last_seen_at": record.last_seen_at,
                }
            return index

    def upsert_jobs(self, source: JobSourceConfig, jobs: list[NormalizedJob]) -> tuple[int, int, int, int]:
        created = 0
        updated = 0
        unchanged = 0
        active_external_ids = {job.external_id for job in jobs}

        with self.session() as session:
            existing_records = session.scalars(select(JobRecord).where(JobRecord.source_id == source.id)).all()
            existing_by_external = {record.external_id: record for record in existing_records}
            now = datetime.now(UTC)

            for job in jobs:
                record = existing_by_external.get(job.external_id)
                if record is None:
                    record = JobRecord(
                        source_id=source.id or 0,
                        external_id=job.external_id,
                        title=job.title,
                        company=job.company,
                        location=job.location,
                        remote_mode=job.remote_mode,
                        job_type=job.job_type,
                        clearance_terms=job.clearance_terms,
                        posted_at=job.posted_at,
                        url=job.url,
                        description=job.description,
                        summary_text=job.summary_text,
                        skills=job.skills,
                        required_skills=job.required_skills,
                        preferred_skills=job.preferred_skills,
                        experience_years=job.experience_years,
                        employment_text=job.employment_text,
                        metadata_json=job.metadata,
                        content_hash=job.content_hash,
                        embedding=job.embedding,
                        active=True,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                    session.add(record)
                    created += 1
                    continue

                record.last_seen_at = now
                record.active = True
                if record.content_hash == job.content_hash:
                    unchanged += 1
                    continue

                record.title = job.title
                record.company = job.company
                record.location = job.location
                record.remote_mode = job.remote_mode
                record.job_type = job.job_type
                record.clearance_terms = job.clearance_terms
                record.posted_at = job.posted_at
                record.url = job.url
                record.description = job.description
                record.summary_text = job.summary_text
                record.skills = job.skills
                record.required_skills = job.required_skills
                record.preferred_skills = job.preferred_skills
                record.experience_years = job.experience_years
                record.employment_text = job.employment_text
                record.metadata_json = job.metadata
                record.content_hash = job.content_hash
                record.embedding = job.embedding
                record.last_updated_at = now
                updated += 1

            deactivated = 0
            for record in existing_records:
                if record.external_id not in active_external_ids and record.active:
                    record.active = False
                    deactivated += 1

        return created, updated, unchanged, deactivated

    def list_jobs(self, *, active_only: bool = True, source_ids: list[int] | None = None) -> list[NormalizedJob]:
        with self.session() as session:
            query = select(JobRecord, SourceRecord).join(SourceRecord, JobRecord.source_id == SourceRecord.id)
            if active_only:
                query = query.where(JobRecord.active.is_(True))
            if source_ids:
                query = query.where(JobRecord.source_id.in_(source_ids))
            rows = session.execute(query.order_by(JobRecord.last_seen_at.desc(), JobRecord.id.desc())).all()
            return [self._job_from_record(job_record, source_record) for job_record, source_record in rows]

    def save_job_embeddings(self, embeddings: dict[int, list[float]]) -> None:
        if not embeddings:
            return
        with self.session() as session:
            for job_id, embedding in embeddings.items():
                record = session.get(JobRecord, job_id)
                if record:
                    record.embedding = embedding

    def clear_jobs_for_source(self, source_id: int) -> None:
        with self.session() as session:
            session.execute(delete(JobRecord).where(JobRecord.source_id == source_id))

    def _migrate_db(self) -> None:
        inspector = inspect(self.engine)
        if not inspector.has_table("sources"):
            return
        source_columns = {column["name"] for column in inspector.get_columns("sources")}
        statements: list[str] = []
        if "max_pages" not in source_columns:
            statements.append(f"ALTER TABLE sources ADD COLUMN max_pages INTEGER DEFAULT {DEFAULT_SOURCE_MAX_PAGES}")
        if "request_delay_ms" not in source_columns:
            statements.append(
                f"ALTER TABLE sources ADD COLUMN request_delay_ms INTEGER DEFAULT {DEFAULT_SOURCE_REQUEST_DELAY_MS}"
            )
        if "use_browser_profile" not in source_columns:
            statements.append("ALTER TABLE sources ADD COLUMN use_browser_profile BOOLEAN DEFAULT 0")
        if not statements:
            return
        with self.engine.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)

    @staticmethod
    def _resume_from_record(record: ResumeRecord) -> ResumeProfile:
        return ResumeProfile(
            id=record.id,
            filename=record.filename,
            file_path=record.file_path,
            file_hash=record.file_hash,
            raw_text=record.raw_text,
            summary_text=record.summary_text,
            skills=list(record.skills or []),
            tools=list(record.tools or []),
            experience_years=float(record.experience_years or 0.0),
            experience_spans=list(record.experience_spans or []),
            sections=dict(record.sections or {}),
            embedding=list(record.embedding) if record.embedding else None,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _source_from_record(record: SourceRecord) -> JobSourceConfig:
        return JobSourceConfig(
            id=record.id,
            name=record.name,
            source_type=record.source_type,
            url=record.url,
            identifier=record.identifier,
            enabled=record.enabled,
            use_playwright=record.use_playwright,
            use_browser_profile=getattr(record, "use_browser_profile", False),
            refresh_minutes=record.refresh_minutes,
            max_pages=record.max_pages or DEFAULT_SOURCE_MAX_PAGES,
            request_delay_ms=record.request_delay_ms or DEFAULT_SOURCE_REQUEST_DELAY_MS,
            notes=record.notes,
            headers=dict(record.headers or {}),
            etag=record.etag,
            last_modified=record.last_modified,
            last_scan_at=record.last_scan_at,
            last_status=record.last_status,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _job_from_record(record: JobRecord, source: SourceRecord) -> NormalizedJob:
        return NormalizedJob(
            id=record.id,
            source_id=record.source_id,
            source_name=source.name,
            source_type=source.source_type,
            external_id=record.external_id,
            title=record.title,
            company=record.company,
            location=record.location,
            remote_mode=record.remote_mode,
            job_type=record.job_type,
            clearance_terms=list(record.clearance_terms or []),
            posted_at=record.posted_at,
            url=record.url,
            description=record.description,
            summary_text=record.summary_text,
            skills=list(record.skills or []),
            required_skills=list(record.required_skills or []),
            preferred_skills=list(record.preferred_skills or []),
            experience_years=record.experience_years,
            employment_text=record.employment_text,
            metadata=dict(record.metadata_json or {}),
            content_hash=record.content_hash,
            active=record.active,
            embedding=list(record.embedding) if record.embedding else None,
            first_seen_at=record.first_seen_at,
            last_seen_at=record.last_seen_at,
            last_updated_at=record.last_updated_at,
        )
