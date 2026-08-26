import asyncio

from app.core.config import get_settings
from app.db.session import Database
from app.jobs.pipeline import DefaultCompanyAnalyzer, SearchPipeline
from app.scoring.service import ScoringRules
from app.sheets.client import GoogleSheetsClient
from app.sheets.exporter import SheetsExporter
from app.sources.registry import build_source_registry


def run_search_job(job_id: int) -> None:
    asyncio.run(_run_search_job(job_id))


async def _run_search_job(job_id: int) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    sources = build_source_registry(settings)
    analyzer = DefaultCompanyAnalyzer.from_settings(settings)
    sheets = None
    try:
        if settings.sheets_enabled:
            sheets_client = await GoogleSheetsClient.create(settings)
            sheets = SheetsExporter(sheets_client, settings)
        pipeline = SearchPipeline(
            database.session_factory,
            sources,
            analyzer,
            ScoringRules.load(settings.scoring_rules_file),
            settings,
            sheets,
        )
        await pipeline.run(job_id)
    finally:
        await asyncio.gather(
            *(source.aclose() for source in sources.values()),
            return_exceptions=True,
        )
        await analyzer.aclose()
        await database.dispose()
