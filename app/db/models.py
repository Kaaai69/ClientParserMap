from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    CMS,
    ContactsAccess,
    ContactType,
    JobStage,
    JobStatus,
    LeadState,
    SourceName,
    WebsiteStatus,
    WebsiteType,
)
from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


def enum_type(enum: type[Enum], name: str) -> SAEnum:
    return SAEnum(
        enum,
        name=name,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda members: [str(member.value) for member in members],
    )


PK_TYPE = BigInteger().with_variant(Integer, "sqlite")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SearchJob(Base):
    __tablename__ = "search_jobs"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    city: Mapped[str] = mapped_column(String(200))
    query: Mapped[str] = mapped_column(String(300))
    min_rating: Mapped[float | None] = mapped_column(Float)
    min_reviews: Mapped[int | None] = mapped_column(Integer)
    max_results: Mapped[int] = mapped_column(Integer)
    requested_sources: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[JobStatus] = mapped_column(
        enum_type(JobStatus, "job_status"), default=JobStatus.PENDING, index=True
    )
    stage: Mapped[JobStage] = mapped_column(
        enum_type(JobStage, "job_stage"), default=JobStage.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    filtered_out_count: Mapped[int] = mapped_column(Integer, default=0)
    unique_count: Mapped[int] = mapped_column(Integer, default=0)
    analyzed_count: Mapped[int] = mapped_column(Integer, default=0)
    lead_count: Mapped[int] = mapped_column(Integer, default=0)
    contactable_lead_count: Mapped[int] = mapped_column(Integer, default=0)
    exported_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    source_states: Mapped[list[SearchJobSource]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )
    outbox: Mapped[JobOutbox] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )
    company_links: Mapped[list[SearchJobCompany]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class SearchJobSource(Base, TimestampMixin):
    __tablename__ = "search_job_sources"
    __table_args__ = (UniqueConstraint("search_job_id", "source"),)

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    search_job_id: Mapped[int] = mapped_column(
        ForeignKey("search_jobs.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[SourceName] = mapped_column(enum_type(SourceName, "source_name"))
    status: Mapped[JobStatus] = mapped_column(
        enum_type(JobStatus, "source_job_status"), default=JobStatus.PENDING
    )
    next_cursor: Mapped[str | None] = mapped_column(Text)
    exhausted: Mapped[bool] = mapped_column(Boolean, default=False)
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1000))

    job: Mapped[SearchJob] = relationship(back_populates="source_states")


class JobOutbox(Base):
    __tablename__ = "job_outbox"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    search_job_id: Mapped[int] = mapped_column(
        ForeignKey("search_jobs.id", ondelete="CASCADE"), unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1000))

    job: Mapped[SearchJob] = relationship(back_populates="outbox")


class Company(Base, TimestampMixin):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(500))
    normalized_name: Mapped[str] = mapped_column(String(500), index=True)
    primary_category: Mapped[str | None] = mapped_column(String(300), index=True)
    city: Mapped[str] = mapped_column(String(200), index=True)
    address: Mapped[str | None] = mapped_column(String(1000))
    normalized_address: Mapped[str | None] = mapped_column(String(1000), index=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    canonical_website: Mapped[str | None] = mapped_column(String(2000))
    registrable_domain: Mapped[str | None] = mapped_column(String(300), index=True)
    rating: Mapped[float | None] = mapped_column(Float)
    reviews_count: Mapped[int | None] = mapped_column(Integer)
    website_status: Mapped[WebsiteStatus] = mapped_column(
        enum_type(WebsiteStatus, "website_status"), default=WebsiteStatus.NO_WEBSITE, index=True
    )
    website_type: Mapped[WebsiteType] = mapped_column(
        enum_type(WebsiteType, "website_type"), default=WebsiteType.NORMAL
    )
    cms: Mapped[CMS] = mapped_column(
        enum_type(CMS, "cms"), default=CMS.CUSTOM_OR_UNKNOWN, index=True
    )
    cms_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    https_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    site_opportunity_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    contactability_score: Mapped[int] = mapped_column(Integer, default=0)
    lead_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    contacts_found: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    contact_count: Mapped[int] = mapped_column(Integer, default=0)
    preferred_contact_type: Mapped[ContactType | None] = mapped_column(
        enum_type(ContactType, "preferred_contact_type")
    )
    preferred_contact_value: Mapped[str | None] = mapped_column(String(1000))
    lead_state: Mapped[LeadState] = mapped_column(
        enum_type(LeadState, "lead_state"), default=LeadState.BELOW_THRESHOLD, index=True
    )
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    sources: Mapped[list[CompanySource]] = relationship(
        back_populates="company", cascade="all, delete-orphan", lazy="selectin"
    )
    contacts: Mapped[list[CompanyContact]] = relationship(
        back_populates="company", cascade="all, delete-orphan", lazy="selectin"
    )
    website_checks: Mapped[list[WebsiteCheck]] = relationship(
        back_populates="company", cascade="all, delete-orphan", lazy="selectin"
    )
    job_links: Mapped[list[SearchJobCompany]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_companies_name_address", "normalized_name", "normalized_address"),
        Index("ix_companies_city_score", "city", "site_opportunity_score"),
    )


class CompanySource(Base, TimestampMixin):
    __tablename__ = "company_sources"
    __table_args__ = (UniqueConstraint("source", "source_id"),)

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[SourceName] = mapped_column(enum_type(SourceName, "company_source_name"))
    source_id: Mapped[str] = mapped_column(String(300))
    name: Mapped[str] = mapped_column(String(500))
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    address: Mapped[str | None] = mapped_column(String(1000))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    rating: Mapped[float | None] = mapped_column(Float)
    reviews_count: Mapped[int | None] = mapped_column(Integer)
    working_hours: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    contacts_access: Mapped[ContactsAccess] = mapped_column(
        enum_type(ContactsAccess, "contacts_access"), default=ContactsAccess.FULL
    )

    company: Mapped[Company] = relationship(back_populates="sources")


class CompanyContact(Base, TimestampMixin):
    __tablename__ = "company_contacts"
    __table_args__ = (UniqueConstraint("company_id", "type", "normalized_value"),)

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[ContactType] = mapped_column(enum_type(ContactType, "contact_type"), index=True)
    value: Mapped[str] = mapped_column(String(1000))
    normalized_value: Mapped[str] = mapped_column(String(1000), index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    company: Mapped[Company] = relationship(back_populates="contacts")
    sources: Mapped[list[CompanyContactSource]] = relationship(
        back_populates="contact", cascade="all, delete-orphan", lazy="selectin"
    )


class CompanyContactSource(Base):
    __tablename__ = "company_contact_sources"
    __table_args__ = (UniqueConstraint("contact_id", "source"),)

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("company_contacts.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    contact: Mapped[CompanyContact] = relationship(back_populates="sources")


class WebsiteCheck(Base):
    __tablename__ = "website_checks"

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    requested_url: Mapped[str | None] = mapped_column(String(2000))
    final_url: Mapped[str | None] = mapped_column(String(2000))
    status: Mapped[WebsiteStatus] = mapped_column(enum_type(WebsiteStatus, "check_status"))
    http_status: Mapped[int | None] = mapped_column(Integer)
    https_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    redirect_count: Mapped[int] = mapped_column(Integer, default=0)
    response_time_ms: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(300))
    cms: Mapped[CMS] = mapped_column(enum_type(CMS, "check_cms"), default=CMS.CUSTOM_OR_UNKNOWN)
    cms_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    website_type: Mapped[WebsiteType] = mapped_column(
        enum_type(WebsiteType, "check_website_type"), default=WebsiteType.NORMAL
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )

    company: Mapped[Company] = relationship(back_populates="website_checks")


class SearchJobCompany(Base, TimestampMixin):
    __tablename__ = "search_job_companies"
    __table_args__ = (UniqueConstraint("search_job_id", "company_id"),)

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    search_job_id: Mapped[int] = mapped_column(
        ForeignKey("search_jobs.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    processing_stage: Mapped[JobStage] = mapped_column(
        enum_type(JobStage, "company_processing_stage"), default=JobStage.COLLECTING
    )
    first_source: Mapped[SourceName | None] = mapped_column(
        enum_type(SourceName, "first_discovery_source")
    )

    job: Mapped[SearchJob] = relationship(back_populates="company_links")
    company: Mapped[Company] = relationship(back_populates="job_links")


class SheetExport(Base):
    __tablename__ = "sheet_exports"
    __table_args__ = (
        UniqueConstraint("spreadsheet_id", "worksheet_name", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    spreadsheet_id: Mapped[str] = mapped_column(String(500))
    worksheet_name: Mapped[str] = mapped_column(String(300))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int] = mapped_column(BigInteger)
    row_number: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    exported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
