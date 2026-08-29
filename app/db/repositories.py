from datetime import UTC, datetime
from typing import Final, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import ContactType, JobStage, JobStatus, SourceName
from app.db.models import (
    Company,
    CompanyContact,
    CompanyContactSource,
    CompanySource,
    JobOutbox,
    SearchJob,
    SearchJobCompany,
    SearchJobSource,
)
from app.schemas.domain import NormalizedCompany, SearchCriteria, SourceCompany

JOB_COUNTERS: Final = {
    "found_count",
    "filtered_out_count",
    "unique_count",
    "analyzed_count",
    "lead_count",
    "contactable_lead_count",
    "exported_count",
    "error_count",
}


class SearchJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_with_outbox(
        self,
        criteria: SearchCriteria,
        sources: tuple[SourceName, ...],
    ) -> SearchJob:
        job = SearchJob(
            city=criteria.city,
            query=criteria.query,
            min_rating=criteria.min_rating,
            min_reviews=criteria.min_reviews,
            max_results=criteria.max_results,
            requested_sources=[source.value for source in sources],
        )
        job.source_states = [SearchJobSource(source=source) for source in sources]
        job.outbox = JobOutbox()
        self.session.add(job)
        await self.session.flush()
        return job

    async def get(self, job_id: int, *, for_update: bool = False) -> SearchJob | None:
        statement = (
            select(SearchJob)
            .where(SearchJob.id == job_id)
            .options(selectinload(SearchJob.source_states), selectinload(SearchJob.outbox))
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(SearchJob | None, await self.session.scalar(statement))

    async def list_recent(self, *, limit: int, offset: int) -> tuple[tuple[SearchJob, ...], int]:
        total = int(await self.session.scalar(select(func.count()).select_from(SearchJob)) or 0)
        jobs = (
            await self.session.scalars(
                select(SearchJob).order_by(SearchJob.id.desc()).limit(limit).offset(offset)
            )
        ).all()
        return tuple(jobs), total

    async def set_stage(self, job_id: int, stage: JobStage) -> None:
        job = await self.get(job_id, for_update=True)
        if job is None:
            raise LookupError(f"search job {job_id} not found")
        job.stage = stage
        job.updated_at = datetime.now(UTC)
        if job.status is JobStatus.PENDING:
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now(UTC)

    async def increment(self, job_id: int, **counters: int) -> None:
        invalid = set(counters) - JOB_COUNTERS
        if invalid:
            raise ValueError(f"unknown counters: {', '.join(sorted(invalid))}")
        job = await self.get(job_id, for_update=True)
        if job is None:
            raise LookupError(f"search job {job_id} not found")
        for name, amount in counters.items():
            setattr(job, name, getattr(job, name) + amount)

    async def finish(self, job_id: int, status: JobStatus) -> None:
        job = await self.get(job_id, for_update=True)
        if job is None:
            raise LookupError(f"search job {job_id} not found")
        job.status = status
        job.stage = JobStage.FINISHED
        job.finished_at = datetime.now(UTC)


class CompanyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        record: SourceCompany,
        normalized: NormalizedCompany,
    ) -> Company:
        company = Company(
            name=record.name,
            normalized_name=normalized.name,
            primary_category=record.categories[0] if record.categories else None,
            city=record.city,
            address=record.address,
            normalized_address=normalized.address,
            latitude=record.latitude,
            longitude=record.longitude,
            canonical_website=normalized.websites[0] if normalized.websites else None,
            registrable_domain=normalized.domains[0] if normalized.domains else None,
            rating=record.rating,
            reviews_count=record.reviews_count,
        )
        self.session.add(company)
        await self.session.flush()
        await self.attach_source(company.id, record)
        for contact in normalized.contacts:
            await self.upsert_contact(
                company.id,
                contact.type,
                contact.value,
                contact.normalized_value,
                record.source,
                is_primary=contact.is_primary,
            )
        return company

    async def attach_source(self, company_id: int, record: SourceCompany) -> CompanySource:
        existing = await self.session.scalar(
            select(CompanySource).where(
                CompanySource.source == record.source,
                CompanySource.source_id == record.source_id,
            )
        )
        if existing is not None:
            return existing
        source = CompanySource(
            company_id=company_id,
            source=record.source,
            source_id=record.source_id,
            name=record.name,
            categories=list(record.categories),
            address=record.address,
            latitude=record.latitude,
            longitude=record.longitude,
            rating=record.rating,
            reviews_count=record.reviews_count,
            working_hours=record.working_hours,
            contacts_access=record.contacts_access,
        )
        self.session.add(source)
        await self.session.flush()
        return source

    async def upsert_contact(
        self,
        company_id: int,
        type: ContactType,
        value: str,
        normalized: str,
        source: SourceName | str,
        *,
        is_primary: bool = False,
    ) -> CompanyContact:
        contact = await self.session.scalar(
            select(CompanyContact)
            .where(
                CompanyContact.company_id == company_id,
                CompanyContact.type == type,
                CompanyContact.normalized_value == normalized,
            )
            .options(selectinload(CompanyContact.sources))
        )
        if contact is None:
            contact = CompanyContact(
                company_id=company_id,
                type=type,
                value=value,
                normalized_value=normalized,
                is_primary=is_primary,
                sources=[],
            )
            self.session.add(contact)
            await self.session.flush()
        elif is_primary:
            contact.is_primary = True

        source_value = source.value if isinstance(source, SourceName) else source
        if source_value not in {item.source for item in contact.sources}:
            evidence = CompanyContactSource(contact_id=contact.id, source=source_value)
            self.session.add(evidence)
            contact.sources.append(evidence)
            await self.session.flush()
        return contact

    async def link_job(self, job_id: int, company_id: int) -> SearchJobCompany:
        existing = await self.session.scalar(
            select(SearchJobCompany).where(
                SearchJobCompany.search_job_id == job_id,
                SearchJobCompany.company_id == company_id,
            )
        )
        if existing is not None:
            return existing
        link = SearchJobCompany(search_job_id=job_id, company_id=company_id)
        self.session.add(link)
        await self.session.flush()
        return link

    async def get_detail(self, company_id: int) -> Company | None:
        result = await self.session.scalar(
            select(Company)
            .where(Company.id == company_id)
            .options(
                selectinload(Company.sources),
                selectinload(Company.contacts).selectinload(CompanyContact.sources),
                selectinload(Company.website_checks),
            )
        )
        return result
