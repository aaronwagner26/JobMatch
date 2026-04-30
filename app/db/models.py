from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ResumeRecord(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    certifications: Mapped[list[str]] = mapped_column(JSON, default=list)
    clearance_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    recent_titles: Mapped[list[str]] = mapped_column(JSON, default=list)
    experience_years: Mapped[float] = mapped_column(Float, default=0.0)
    experience_spans: Mapped[list[dict]] = mapped_column(JSON, default=list)
    sections: Mapped[dict] = mapped_column(JSON, default=dict)
    application_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SourceRecord(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    use_playwright: Mapped[bool] = mapped_column(Boolean, default=False)
    use_browser_profile: Mapped[bool] = mapped_column(Boolean, default=False)
    refresh_minutes: Mapped[int] = mapped_column(Integer, default=180)
    max_pages: Mapped[int] = mapped_column(Integer, default=3)
    request_delay_ms: Mapped[int] = mapped_column(Integer, default=750)
    notes: Mapped[str] = mapped_column(Text, default="")
    headers: Mapped[dict] = mapped_column(JSON, default=dict)
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    jobs: Mapped[list["JobRecord"]] = relationship(back_populates="source", cascade="all, delete-orphan")
    scans: Mapped[list["ScanRecord"]] = relationship(back_populates="source", cascade="all, delete-orphan")


class JobRecord(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_source_external_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company: Mapped[str] = mapped_column(String(255), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    remote_mode: Mapped[str] = mapped_column(String(32), default="unknown")
    job_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    clearance_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    salary_interval: Mapped[str | None] = mapped_column(String(16), nullable=True)
    salary_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    url: Mapped[str] = mapped_column(String(1200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    summary_text: Mapped[str] = mapped_column(Text, default="")
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    required_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    experience_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    employment_text: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    application_status: Mapped[str] = mapped_column(String(32), default="not_applied")
    application_confirmation_needed: Mapped[bool] = mapped_column(Boolean, default=False)
    application_last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    application_status_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    source: Mapped["SourceRecord"] = relationship(back_populates="jobs")


class ScanRecord(Base):
    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="running")
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    jobs_created: Mapped[int] = mapped_column(Integer, default=0)
    jobs_updated: Mapped[int] = mapped_column(Integer, default=0)
    jobs_unchanged: Mapped[int] = mapped_column(Integer, default=0)
    jobs_deactivated: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[SourceRecord | None] = relationship(back_populates="scans")


class AppSettingRecord(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
