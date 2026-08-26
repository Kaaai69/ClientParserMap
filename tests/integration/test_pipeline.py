from collections.abc import AsyncIterator, Sequence
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.enums import (
    CMS,
    ContactType,
    JobStatus,
    LeadState,
    SourceName,
    WebsiteStatus,
    WebsiteType,
)
from app.db.base import Base
from app.db.models import Company, SearchJob, SheetExport
from app.db.repositories import SearchJobRepository
from app.jobs.pipeline import CompanyAnalysis, SearchPipeline
from app.schemas.domain import ContactValue, SearchCriteria, SourceCompany, SourcePage
from app.scoring.service import ScoringRules
from app.sheets.client import SheetRow
from app.sheets.exporter import SheetsExporter
from app.website_analyzer.checker import WebsiteFetchResult
from app.website_analyzer.cms_detector import CmsDetection
from app.website_analyzer.website_type import WebsiteClassification


class OnePageSource:
    def __init__(self, name: SourceName, company: SourceCompany) -> None:
        self.name = name
        self.company = company
        self.calls = 0

    async def search_page(
        self,
        criteria: SearchCriteria,
        cursor: str | None,
    ) -> SourcePage:
        self.calls += 1
        return SourcePage(items=(self.company,), exhausted=True)

    async def aclose(self) -> None:
        return None


class FakeAnalyzer:
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, company: Company) -> CompanyAnalysis:
        self.calls += 1
        url = company.canonical_website
        return CompanyAnalysis(
            fetch=WebsiteFetchResult(
                requested_url=url,
                final_url=url,
                status=WebsiteStatus.ONLINE,
                http_status=200,
                is_https=bool(url and urlsplit(url).scheme == "https"),
                content_type="text/html",
                html="<html><body>Сайт в разработке</body></html>",
            ),
            cms=CmsDetection(CMS.TILDA, 0.9, ("Tilda CDN",)),
            classification=WebsiteClassification(
                status=WebsiteStatus.PLACEHOLDER,
                website_type=WebsiteType.NORMAL,
                reasons=("сайт в разработке",),
            ),
            contacts=(
                ContactValue(
                    type=ContactType.EMAIL,
                    value="sales@example.ru",
                    normalized_value="sales@example.ru",
                ),
            ),
        )


class InMemorySheets:
    def __init__(self) -> None:
        self.data: dict[str, list[dict[str, str]]] = {}

    async def ensure_worksheet(self, name: str, headers: Sequence[str]) -> None:
        self.data.setdefault(name, [])

    async def read_records(self, name: str) -> list[SheetRow]:
        return [
            SheetRow(row_number=index + 2, values=dict(values))
            for index, values in enumerate(self.data.get(name, []))
        ]

    async def read_row(
        self,
        name: str,
        row_number: int,
        headers: Sequence[str],
    ) -> SheetRow:
        return SheetRow(
            row_number=row_number,
            values=dict(self.data[name][row_number - 2]),
        )

    async def append_row(
        self,
        name: str,
        headers: Sequence[str],
        values: dict[str, str],
    ) -> int:
        self.data[name].append(dict(values))
        return len(self.data[name]) + 1

    async def update_row(
        self,
        name: str,
        row_number: int,
        headers: Sequence[str],
        values: dict[str, str],
    ) -> None:
        self.data[name][row_number - 2] = dict(values)

    def rows(self, name: str) -> list[dict[str, str]]:
        return self.data.get(name, [])


async def database_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_pipeline_deduplicates_analyzes_scores_and_exports_once() -> None:
    async for session_factory in database_factory():
        criteria = SearchCriteria(city="Москва", query="детейлинг", max_results=300)
        async with session_factory() as session:
            job = await SearchJobRepository(session).create_with_outbox(
                criteria,
                (SourceName.GOOGLE, SourceName.TWO_GIS),
            )
            await session.commit()
            job_id = job.id

        google = SourceCompany(
            source=SourceName.GOOGLE,
            source_id="google-1",
            name="Nyra Auto",
            city="Москва",
            categories=("Детейлинг",),
            address="Тверская улица, 10",
            websites=("https://example.ru",),
            rating=4.9,
            reviews_count=137,
        )
        two_gis = google.model_copy(update={"source": SourceName.TWO_GIS, "source_id": "2gis-1"})
        sources = {
            SourceName.GOOGLE: OnePageSource(SourceName.GOOGLE, google),
            SourceName.TWO_GIS: OnePageSource(SourceName.TWO_GIS, two_gis),
        }
        analyzer = FakeAnalyzer()
        sheets = InMemorySheets()
        settings = Settings(_env_file=None, google_sheets_spreadsheet_id="sheet-1")
        pipeline = SearchPipeline(
            session_factory,
            sources,
            analyzer,
            ScoringRules.load(settings.scoring_rules_file),
            settings,
            SheetsExporter(sheets, settings),
        )

        await pipeline.run(job_id)
        await pipeline.run(job_id)

        async with session_factory() as session:
            cached_job = await SearchJobRepository(session).create_with_outbox(
                criteria,
                (SourceName.GOOGLE, SourceName.TWO_GIS),
            )
            await session.commit()
            cached_job_id = cached_job.id
        await pipeline.run(cached_job_id)

        async with session_factory() as session:
            stored_job = await session.get(SearchJob, job_id)
            stored_company = await session.scalar(select(Company))
            export_count = await session.scalar(select(func.count()).select_from(SheetExport))
            assert stored_job is not None
            assert stored_company is not None
            assert stored_job.status is JobStatus.COMPLETED
            assert stored_job.found_count == 2
            assert stored_job.unique_count == 1
            assert stored_company.lead_state is LeadState.QUALIFIED
            assert stored_company.preferred_contact_value == "sales@example.ru"
            assert export_count == 4

        assert analyzer.calls == 1
        assert len(sheets.rows("Все компании")) == 1
        assert len(sheets.rows("Готовые лиды")) == 1
        assert len(sheets.rows("Запуски поиска")) == 2
