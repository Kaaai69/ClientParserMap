import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.enums import (
    CMS,
    ContactType,
    JobStage,
    JobStatus,
    LeadState,
    SourceName,
    WebsiteStatus,
    WebsiteType,
)
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.models import (
    Company,
    CompanyContact,
    SearchJob,
    SearchJobCompany,
    SearchJobSource,
    SheetExport,
    WebsiteCheck,
)
from app.db.repositories import CompanyRepository, SearchJobRepository
from app.deduplication.service import DeduplicationService
from app.schemas.domain import ContactValue, SearchCriteria
from app.scoring.service import ScoringInput, ScoringRules, score_company
from app.sheets.exporter import (
    CompanySheetRecord,
    SearchJobSheetRecord,
    SheetsExporter,
    SheetWriteResult,
)
from app.sources.base import LeadSource
from app.website_analyzer.checker import WebsiteFetcher, WebsiteFetchResult
from app.website_analyzer.cms_detector import CmsDetection, detect_cms
from app.website_analyzer.contacts import ContactCrawler
from app.website_analyzer.security import PinnedAsyncHTTPTransport, SafeUrlPolicy
from app.website_analyzer.website_type import WebsiteClassification, classify_website

TERMINAL_JOB_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.COMPLETED_WITH_ERRORS,
    JobStatus.FAILED,
}
logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CompanyAnalysis:
    fetch: WebsiteFetchResult
    cms: CmsDetection
    classification: WebsiteClassification
    contacts: tuple[ContactValue, ...]


class CompanyAnalyzer(Protocol):
    async def analyze(self, company: Company) -> CompanyAnalysis: ...


class DefaultCompanyAnalyzer:
    def __init__(self, fetcher: WebsiteFetcher, crawler: ContactCrawler) -> None:
        self._fetcher = fetcher
        self._crawler = crawler

    @classmethod
    def from_settings(cls, settings: Settings) -> "DefaultCompanyAnalyzer":
        limits = httpx.Limits(
            max_connections=settings.max_concurrent_website_checks,
            max_keepalive_connections=settings.max_concurrent_website_checks,
        )
        policy = SafeUrlPolicy()
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.website_timeout_seconds),
            transport=PinnedAsyncHTTPTransport(policy, limits=limits),
        )
        fetcher = WebsiteFetcher(
            client,
            policy,
            max_redirects=settings.max_website_redirects,
            max_html_bytes=settings.max_html_bytes,
        )
        return cls(
            fetcher,
            ContactCrawler(fetcher, max_pages=settings.max_contact_pages),
        )

    async def analyze(self, company: Company) -> CompanyAnalysis:
        fetch = await self._fetcher.fetch(company.canonical_website)
        if (
            fetch.status is WebsiteStatus.ONLINE
            and fetch.html is not None
            and fetch.final_url is not None
        ):
            cms = detect_cms(fetch.html, dict(fetch.headers), fetch.final_url)
            classification = classify_website(fetch.final_url, fetch.html)
            contacts = await self._crawler.crawl(fetch)
        else:
            cms = CmsDetection(CMS.CUSTOM_OR_UNKNOWN, 0.0)
            classification = WebsiteClassification(
                status=fetch.status,
                website_type=WebsiteType.NORMAL,
            )
            contacts = ()
        return CompanyAnalysis(fetch, cms, classification, contacts)

    async def aclose(self) -> None:
        await self._fetcher.aclose()


class SearchPipeline:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sources: Mapping[SourceName, LeadSource],
        analyzer: CompanyAnalyzer,
        scoring_rules: ScoringRules,
        settings: Settings,
        sheets: SheetsExporter | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._sources = sources
        self._analyzer = analyzer
        self._rules = scoring_rules
        self._settings = settings
        self._sheets = sheets
        self._spreadsheet_id = settings.google_sheets_spreadsheet_id

    async def run(self, job_id: int) -> None:
        async with self._session_factory() as session:
            job = await SearchJobRepository(session).get(job_id, for_update=True)
            if job is None:
                raise LookupError(f"search job {job_id} not found")
            if job.status in TERMINAL_JOB_STATUSES:
                return
            job.status = JobStatus.RUNNING
            job.stage = JobStage.COLLECTING
            job.started_at = job.started_at or datetime.now(UTC)
            await session.commit()
            logger.info("search_job_started", search_job_id=job.id, stage=job.stage.value)

            await self._sync_job_safely(session, job)
            await self._collect(session, job)
            job.stage = JobStage.DEDUPLICATING
            await session.commit()
            await self._sync_job_safely(session, job)

            if job.unique_count == 0 and all(
                state.status is JobStatus.FAILED for state in job.source_states
            ):
                job.status = JobStatus.FAILED
                job.stage = JobStage.FINISHED
                job.finished_at = datetime.now(UTC)
                await session.commit()
                await self._sync_job_safely(session, job)
                logger.info(
                    "search_job_finished",
                    search_job_id=job.id,
                    stage=job.stage.value,
                    status=job.status.value,
                    result_count=job.unique_count,
                    error_count=job.error_count,
                )
                return

            await self._analyze(session, job)
            await self._score(session, job)
            await self._export(session, job)

            job.status = JobStatus.COMPLETED_WITH_ERRORS if job.error_count else JobStatus.COMPLETED
            job.stage = JobStage.FINISHED
            job.finished_at = datetime.now(UTC)
            await session.commit()
            final_sync_succeeded = await self._sync_job_safely(session, job)
            if not final_sync_succeeded and job.status is JobStatus.COMPLETED:
                job.status = JobStatus.COMPLETED_WITH_ERRORS
                await session.commit()
            logger.info(
                "search_job_finished",
                search_job_id=job.id,
                stage=job.stage.value,
                status=job.status.value,
                result_count=job.unique_count,
                error_count=job.error_count,
            )

    async def _collect(self, session: AsyncSession, job: SearchJob) -> None:
        criteria = SearchCriteria(
            city=job.city,
            query=job.query,
            min_rating=job.min_rating,
            min_reviews=job.min_reviews,
            max_results=job.max_results,
        )
        accepted_total = sum(state.accepted_count for state in job.source_states)
        while accepted_total < job.max_results:
            progressed = False
            for state in job.source_states:
                if state.exhausted or state.status is JobStatus.FAILED:
                    continue
                progressed = True
                source = self._sources.get(state.source)
                if source is None:
                    self._source_failure(job, state, "SOURCE_NOT_CONFIGURED")
                    await session.commit()
                    continue
                state.status = JobStatus.RUNNING
                previous_cursor = state.next_cursor
                try:
                    page = await source.search_page(criteria, state.next_cursor)
                except Exception as error:
                    self._source_failure(job, state, _error_code(error))
                    await session.commit()
                    continue
                logger.info(
                    "source_page_received",
                    search_job_id=job.id,
                    source=state.source.value,
                    result_count=len(page.items),
                )

                for record in page.items:
                    if accepted_total >= job.max_results:
                        break
                    job.found_count += 1
                    state.found_count += 1
                    if not _passes_filters(record.rating, record.reviews_count, criteria):
                        job.filtered_out_count += 1
                        continue
                    company_id = await DeduplicationService(session).upsert_source_company(record)
                    existing_link = await session.scalar(
                        select(SearchJobCompany).where(
                            SearchJobCompany.search_job_id == job.id,
                            SearchJobCompany.company_id == company_id,
                        )
                    )
                    if existing_link is None:
                        session.add(
                            SearchJobCompany(
                                search_job_id=job.id,
                                company_id=company_id,
                                first_source=record.source,
                            )
                        )
                        job.unique_count += 1
                    state.accepted_count += 1
                    accepted_total += 1

                state.next_cursor = page.next_cursor
                state.exhausted = page.exhausted or page.next_cursor is None
                if not state.exhausted and state.next_cursor == previous_cursor:
                    self._source_failure(job, state, "SOURCE_CURSOR_STALLED")
                elif state.exhausted:
                    state.status = JobStatus.COMPLETED
                elif accepted_total >= job.max_results:
                    state.status = JobStatus.COMPLETED
                await session.commit()
                if accepted_total >= job.max_results:
                    break
            if not progressed:
                break

    async def _analyze(self, session: AsyncSession, job: SearchJob) -> None:
        job.stage = JobStage.ANALYZING_WEBSITES
        await session.commit()
        await self._sync_job_safely(session, job)
        links = await self._job_links(session, job.id)
        pending = [
            link
            for link in links
            if link.processing_stage
            in {JobStage.COLLECTING, JobStage.DEDUPLICATING, JobStage.ANALYZING_WEBSITES}
        ]
        uncached: list[SearchJobCompany] = []
        for link in pending:
            if self._has_fresh_website_check(link.company):
                link.processing_stage = JobStage.SCORING
                job.analyzed_count += 1
            else:
                uncached.append(link)
        semaphore = asyncio.Semaphore(self._settings.max_concurrent_website_checks)

        async def analyze_one(
            link: SearchJobCompany,
        ) -> tuple[SearchJobCompany, CompanyAnalysis | Exception]:
            async with semaphore:
                try:
                    return link, await self._analyzer.analyze(link.company)
                except Exception as error:
                    return link, error

        results = await asyncio.gather(*(analyze_one(link) for link in uncached))
        for link, result in results:
            company = link.company
            if isinstance(result, Exception):
                company.website_status = WebsiteStatus.ERROR
                link.processing_stage = JobStage.SCORING
                self._job_error(job, "WEBSITE_ANALYSIS_FAILED", company.id)
                continue
            await self._store_analysis(session, company, result)
            link.processing_stage = JobStage.SCORING
            job.analyzed_count += 1
        await session.commit()

    def _has_fresh_website_check(self, company: Company) -> bool:
        if not company.website_checks:
            return False
        latest_checked_at = max(item.checked_at for item in company.website_checks)
        if latest_checked_at.tzinfo is None:
            latest_checked_at = latest_checked_at.replace(tzinfo=UTC)
        cutoff = datetime.now(UTC) - timedelta(hours=self._settings.website_check_ttl_hours)
        return latest_checked_at >= cutoff

    async def _store_analysis(
        self,
        session: AsyncSession,
        company: Company,
        analysis: CompanyAnalysis,
    ) -> None:
        fetch = analysis.fetch
        company.website_status = analysis.classification.status
        company.website_type = analysis.classification.website_type
        company.cms = analysis.cms.cms
        company.cms_confidence = analysis.cms.confidence
        company.https_enabled = fetch.is_https
        session.add(
            WebsiteCheck(
                company_id=company.id,
                requested_url=fetch.requested_url,
                final_url=fetch.final_url,
                status=analysis.classification.status,
                http_status=fetch.http_status,
                https_enabled=fetch.is_https,
                redirect_count=fetch.redirect_count,
                response_time_ms=fetch.response_time_ms,
                content_type=fetch.content_type,
                cms=analysis.cms.cms,
                cms_confidence=analysis.cms.confidence,
                website_type=analysis.classification.website_type,
                error_code=fetch.error_code,
            )
        )
        companies = CompanyRepository(session)
        for contact in analysis.contacts:
            await companies.upsert_contact(
                company.id,
                contact.type,
                contact.value,
                contact.normalized_value,
                "website",
                is_primary=contact.is_primary,
            )
        await session.flush()
        company.contact_count = int(
            await session.scalar(
                select(func.count())
                .select_from(CompanyContact)
                .where(CompanyContact.company_id == company.id)
            )
            or 0
        )
        company.contacts_found = company.contact_count > 0

    async def _score(self, session: AsyncSession, job: SearchJob) -> None:
        job.stage = JobStage.SCORING
        await session.commit()
        await self._sync_job_safely(session, job)
        links = await self._job_links(session, job.id)
        lead_count = 0
        contactable_count = 0
        for link in links:
            company = link.company
            contacts = tuple(
                ContactValue(
                    type=item.type,
                    value=item.value,
                    normalized_value=item.normalized_value,
                    is_primary=item.is_primary,
                )
                for item in company.contacts
            )
            result = score_company(
                ScoringInput(
                    website_status=company.website_status,
                    website_type=company.website_type,
                    cms=company.cms,
                    rating=company.rating,
                    reviews_count=company.reviews_count,
                    contacts=contacts,
                ),
                self._rules,
                self._settings.lead_score_threshold,
            )
            company.site_opportunity_score = result.site_opportunity_score
            company.contactability_score = result.contactability_score
            company.lead_reasons = list(result.reasons)
            company.preferred_contact_type = result.preferred_contact_type
            company.preferred_contact_value = result.preferred_contact_value
            company.lead_state = result.lead_state
            if link.processing_stage is not JobStage.FINISHED:
                link.processing_stage = JobStage.EXPORTING
            if result.site_opportunity_score >= self._settings.lead_score_threshold:
                lead_count += 1
            if result.lead_state is LeadState.QUALIFIED:
                contactable_count += 1
        job.lead_count = lead_count
        job.contactable_lead_count = contactable_count
        await session.commit()

    async def _export(self, session: AsyncSession, job: SearchJob) -> None:
        job.stage = JobStage.EXPORTING
        await session.commit()
        if self._sheets is None:
            if not any(item.get("code") == "SHEETS_DISABLED" for item in job.errors):
                job.errors = [*job.errors, {"code": "SHEETS_DISABLED", "severity": "warning"}]
            for link in await self._job_links(session, job.id):
                link.processing_stage = JobStage.FINISHED
            await session.commit()
            return

        await self._sync_job_safely(session, job)
        for link in await self._job_links(session, job.id):
            if link.processing_stage is JobStage.FINISHED:
                continue
            try:
                results = await self._sheets.sync_company(_company_sheet_record(job, link))
            except Exception:
                self._job_error(job, "SHEETS_COMPANY_EXPORT_FAILED", link.company_id)
                continue
            for result in results:
                await self._record_export(session, result)
            job.exported_count += len(results)
            link.processing_stage = JobStage.FINISHED
            await session.commit()

    async def _sync_job_safely(self, session: AsyncSession, job: SearchJob) -> bool:
        if self._sheets is None:
            return True
        try:
            result = await self._sheets.sync_job(_job_sheet_record(job))
            await self._record_export(session, result)
            await session.commit()
        except Exception:
            self._job_error(job, "SHEETS_JOB_EXPORT_FAILED")
            await session.commit()
            return False
        return True

    async def _record_export(
        self,
        session: AsyncSession,
        result: SheetWriteResult,
    ) -> None:
        if self._spreadsheet_id is None:
            raise RuntimeError("spreadsheet ID is absent for an enabled exporter")
        export = await session.scalar(
            select(SheetExport).where(
                SheetExport.spreadsheet_id == self._spreadsheet_id,
                SheetExport.worksheet_name == result.worksheet_name,
                SheetExport.entity_type == result.entity_type,
                SheetExport.entity_id == result.entity_id,
            )
        )
        if export is None:
            session.add(
                SheetExport(
                    spreadsheet_id=self._spreadsheet_id,
                    worksheet_name=result.worksheet_name,
                    entity_type=result.entity_type,
                    entity_id=result.entity_id,
                    row_number=result.row_number,
                )
            )
        else:
            export.row_number = result.row_number
            export.exported_at = datetime.now(UTC)

    @staticmethod
    async def _job_links(
        session: AsyncSession,
        job_id: int,
    ) -> list[SearchJobCompany]:
        return list(
            (
                await session.scalars(
                    select(SearchJobCompany)
                    .where(SearchJobCompany.search_job_id == job_id)
                    .order_by(SearchJobCompany.id)
                    .options(
                        selectinload(SearchJobCompany.company).selectinload(Company.sources),
                        selectinload(SearchJobCompany.company).selectinload(Company.contacts),
                        selectinload(SearchJobCompany.company).selectinload(Company.website_checks),
                    )
                    .execution_options(populate_existing=True)
                )
            )
            .unique()
            .all()
        )

    @staticmethod
    def _source_failure(job: SearchJob, state: SearchJobSource, code: str) -> None:
        state.status = JobStatus.FAILED
        state.exhausted = True
        state.error_count += 1
        state.last_error = code
        SearchPipeline._job_error(job, code)

    @staticmethod
    def _job_error(job: SearchJob, code: str, company_id: int | None = None) -> None:
        error: dict[str, str | int] = {"code": code}
        if company_id is not None:
            error["company_id"] = company_id
        job.errors = [*job.errors, error]
        job.error_count += 1


def _passes_filters(
    rating: float | None,
    reviews_count: int | None,
    criteria: SearchCriteria,
) -> bool:
    if criteria.min_rating is not None and (rating is None or rating < criteria.min_rating):
        return False
    return not (
        criteria.min_reviews is not None
        and (reviews_count is None or reviews_count < criteria.min_reviews)
    )


def _error_code(error: Exception) -> str:
    return error.code if isinstance(error, AppError) else type(error).__name__.upper()


def _company_sheet_record(job: SearchJob, link: SearchJobCompany) -> CompanySheetRecord:
    company = link.company
    contacts_by_type: dict[ContactType, list[CompanyContact]] = {}
    for contact in company.contacts:
        contacts_by_type.setdefault(contact.type, []).append(contact)
    phones = contacts_by_type.get(ContactType.PHONE, [])
    primary_phone = next((item.value for item in phones if item.is_primary), None)
    if primary_phone is None and phones:
        primary_phone = phones[0].value
    return CompanySheetRecord(
        id=company.id,
        discovered_at=company.discovered_at,
        updated_at=company.updated_at,
        name=company.name,
        search_query=job.query,
        category=company.primary_category,
        city=company.city,
        address=company.address,
        primary_phone=primary_phone,
        additional_phones=tuple(item.value for item in phones if item.value != primary_phone),
        whatsapp=_contact_values(contacts_by_type, ContactType.WHATSAPP),
        telegram=_contact_values(contacts_by_type, ContactType.TELEGRAM),
        emails=_contact_values(contacts_by_type, ContactType.EMAIL),
        vk=_contact_values(contacts_by_type, ContactType.VK),
        instagram=_contact_values(contacts_by_type, ContactType.INSTAGRAM),
        other_socials=_contact_values(contacts_by_type, ContactType.OTHER),
        website=company.canonical_website,
        primary_source=link.first_source,
        sources=tuple(dict.fromkeys(item.source for item in company.sources)),
        rating=company.rating,
        reviews_count=company.reviews_count,
        website_status=company.website_status,
        cms=company.cms,
        website_type=company.website_type,
        https_enabled=company.https_enabled,
        site_opportunity_score=company.site_opportunity_score,
        contactability_score=company.contactability_score,
        contacts_found=company.contacts_found,
        preferred_contact_type=company.preferred_contact_type,
        preferred_contact_value=company.preferred_contact_value,
        reasons=tuple(company.lead_reasons),
        lead_state=company.lead_state,
    )


def _contact_values(
    grouped: dict[ContactType, list[CompanyContact]],
    contact_type: ContactType,
) -> tuple[str, ...]:
    return tuple(item.value for item in grouped.get(contact_type, []))


def _job_sheet_record(job: SearchJob) -> SearchJobSheetRecord:
    return SearchJobSheetRecord(
        id=job.id,
        created_at=job.created_at,
        finished_at=job.finished_at,
        city=job.city,
        query=job.query,
        sources=tuple(SourceName(value) for value in job.requested_sources),
        min_rating=job.min_rating,
        min_reviews=job.min_reviews,
        max_results=job.max_results,
        status=job.status,
        stage=job.stage,
        found_count=job.found_count,
        filtered_out_count=job.filtered_out_count,
        unique_count=job.unique_count,
        analyzed_count=job.analyzed_count,
        lead_count=job.lead_count,
        contactable_lead_count=job.contactable_lead_count,
        exported_count=job.exported_count,
        error_count=job.error_count,
    )
