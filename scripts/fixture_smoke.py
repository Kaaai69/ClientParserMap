import argparse
import asyncio
import json
from dataclasses import asdict, dataclass

from sqlalchemy import func, select

from app.core.config import Settings
from app.core.enums import CMS, JobStatus, SourceName, WebsiteStatus, WebsiteType
from app.db.base import Base
from app.db.models import Company, SearchJob
from app.db.repositories import SearchJobRepository
from app.db.session import Database
from app.jobs.pipeline import CompanyAnalysis, SearchPipeline
from app.schemas.domain import SearchCriteria, SourceCompany, SourcePage
from app.scoring.service import ScoringRules
from app.website_analyzer.checker import WebsiteFetchResult
from app.website_analyzer.cms_detector import CmsDetection
from app.website_analyzer.website_type import WebsiteClassification


@dataclass(frozen=True, slots=True)
class SmokeResult:
    job_id: int
    status: str
    found_count: int
    unique_count: int
    company_count: int
    duplicate_count: int


class FixtureSource:
    def __init__(self, name: SourceName, company: SourceCompany) -> None:
        self.name = name
        self._company = company

    async def search_page(
        self,
        criteria: SearchCriteria,
        cursor: str | None,
    ) -> SourcePage:
        return SourcePage(items=(self._company,), exhausted=True)

    async def aclose(self) -> None:
        return None


class FixtureAnalyzer:
    async def analyze(self, company: Company) -> CompanyAnalysis:
        return CompanyAnalysis(
            fetch=WebsiteFetchResult(
                requested_url=None,
                final_url=None,
                status=WebsiteStatus.NO_WEBSITE,
            ),
            cms=CmsDetection(CMS.CUSTOM_OR_UNKNOWN, 0.0),
            classification=WebsiteClassification(
                status=WebsiteStatus.NO_WEBSITE,
                website_type=WebsiteType.NORMAL,
            ),
            contacts=(),
        )


def run_fixture_smoke(
    *,
    database_url: str | None = None,
    initialize_schema: bool = False,
) -> SmokeResult:
    return asyncio.run(
        _run_fixture_smoke(
            database_url=database_url,
            initialize_schema=initialize_schema,
        )
    )


async def _run_fixture_smoke(
    *,
    database_url: str | None,
    initialize_schema: bool,
) -> SmokeResult:
    base_settings = Settings(_env_file=None)
    resolved_database_url = database_url or base_settings.database_url
    settings = Settings(_env_file=None, database_url=resolved_database_url)
    database = Database(resolved_database_url)
    try:
        if initialize_schema:
            async with database.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        criteria = SearchCriteria(city="Москва", query="fixture-smoke", max_results=10)
        async with database.session_factory() as session:
            job = await SearchJobRepository(session).create_with_outbox(
                criteria,
                (SourceName.GOOGLE, SourceName.TWO_GIS),
            )
            await session.commit()
            job_id = job.id

        google_company = SourceCompany(
            source=SourceName.GOOGLE,
            source_id="fixture-google-company",
            name="Fixture Company",
            city="Москва",
            categories=("Fixture",),
            address="Fixture Street, 1",
            phones=("+7 999 123-45-67",),
            rating=4.9,
            reviews_count=137,
        )
        two_gis_company = google_company.model_copy(
            update={
                "source": SourceName.TWO_GIS,
                "source_id": "fixture-2gis-company",
            }
        )
        sources = {
            SourceName.GOOGLE: FixtureSource(SourceName.GOOGLE, google_company),
            SourceName.TWO_GIS: FixtureSource(SourceName.TWO_GIS, two_gis_company),
        }
        pipeline = SearchPipeline(
            database.session_factory,
            sources,
            FixtureAnalyzer(),
            ScoringRules.load(settings.scoring_rules_file),
            settings,
        )
        await pipeline.run(job_id)

        async with database.session_factory() as session:
            stored_job = await session.get(SearchJob, job_id)
            company_count = int(
                await session.scalar(select(func.count()).select_from(Company)) or 0
            )
            if stored_job is None:
                raise RuntimeError("fixture smoke job disappeared")
            if stored_job.status not in {
                JobStatus.COMPLETED,
                JobStatus.COMPLETED_WITH_ERRORS,
            }:
                raise RuntimeError(f"fixture smoke failed with status {stored_job.status.value}")
            return SmokeResult(
                job_id=stored_job.id,
                status=stored_job.status.value,
                found_count=stored_job.found_count,
                unique_count=stored_job.unique_count,
                company_count=company_count,
                duplicate_count=max(0, company_count - 1),
            )
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Credential-free pipeline smoke test")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--initialize-schema", action="store_true")
    arguments = parser.parse_args()
    result = run_fixture_smoke(
        database_url=arguments.database_url,
        initialize_schema=arguments.initialize_schema,
    )
    print(json.dumps(asdict(result), ensure_ascii=False))


if __name__ == "__main__":
    main()
