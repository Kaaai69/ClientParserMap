import asyncio
from typing import Annotated

import typer
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings, get_settings
from app.core.enums import JobStatus, SourceName
from app.core.errors import ConfigurationError
from app.core.logging import configure_logging
from app.db.repositories import SearchJobRepository
from app.db.session import Database
from app.jobs.outbox import OutboxDispatcher, RQSearchQueue
from app.schemas.domain import SearchCriteria

app = typer.Typer(help="Поиск и обработка потенциальных клиентов")


class CliSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: int
    status: str
    found_count: int = 0
    unique_count: int = 0
    analyzed_count: int = 0
    lead_count: int = 0
    contactable_lead_count: int = 0
    exported_count: int = 0
    error_count: int = 0


@app.callback()
def root() -> None:
    """Управление поиском потенциальных клиентов."""


@app.command()
def search(
    city: Annotated[str, typer.Option("--city")],
    query: Annotated[str, typer.Option("--query")],
    min_rating: Annotated[float | None, typer.Option("--min-rating")] = None,
    min_reviews: Annotated[int | None, typer.Option("--min-reviews")] = None,
    max_results: Annotated[int, typer.Option("--max-results")] = 500,
    source: Annotated[list[SourceName] | None, typer.Option("--source")] = None,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
) -> None:
    try:
        result = asyncio.run(
            execute_search(
                city=city,
                query=query,
                min_rating=min_rating,
                min_reviews=min_reviews,
                max_results=max_results,
                sources=tuple(source) if source else None,
                no_wait=no_wait,
            )
        )
    except (ConfigurationError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(f"Search job: {result.job_id} ({result.status})")
    if no_wait:
        return
    typer.echo(f"Found: {result.found_count}")
    typer.echo(f"Unique: {result.unique_count}")
    typer.echo(f"Analyzed: {result.analyzed_count}")
    typer.echo(f"Potential leads: {result.lead_count}")
    typer.echo(f"Contactable leads: {result.contactable_lead_count}")
    typer.echo(f"Exported to Google Sheets: {result.exported_count}")
    typer.echo(f"Errors: {result.error_count}")
    if result.status == JobStatus.FAILED.value:
        raise typer.Exit(code=1)


async def execute_search(
    *,
    city: str,
    query: str,
    min_rating: float | None,
    min_reviews: int | None,
    max_results: int,
    sources: tuple[SourceName, ...] | None,
    no_wait: bool,
    settings: Settings | None = None,
) -> CliSearchResult:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    enabled = set(resolved_settings.enabled_sources)
    selected = sources or resolved_settings.enabled_sources
    if not selected:
        raise ConfigurationError("Не настроен ни один источник поиска")
    if set(selected) - enabled:
        raise ConfigurationError("Запрошенный источник не настроен или не разрешён")
    criteria = SearchCriteria(
        city=city,
        query=query,
        min_rating=min_rating,
        min_reviews=min_reviews,
        max_results=max_results,
    )
    database = Database(resolved_settings.database_url)
    queue = RQSearchQueue.from_settings(resolved_settings)
    try:
        async with database.session_factory() as session:
            job = await SearchJobRepository(session).create_with_outbox(criteria, tuple(selected))
            await session.commit()
            job_id = job.id
        await OutboxDispatcher(
            database.session_factory,
            queue,
            job_timeout_seconds=resolved_settings.rq_job_timeout_seconds,
        ).dispatch_pending()
        if no_wait:
            return CliSearchResult(job_id=job_id, status=JobStatus.PENDING.value)
        while True:
            async with database.session_factory() as session:
                current = await SearchJobRepository(session).get(job_id)
                if current is None:
                    raise RuntimeError("Созданный запуск поиска не найден")
                if current.status in {
                    JobStatus.COMPLETED,
                    JobStatus.COMPLETED_WITH_ERRORS,
                    JobStatus.FAILED,
                }:
                    return CliSearchResult(
                        job_id=current.id,
                        status=current.status.value,
                        found_count=current.found_count,
                        unique_count=current.unique_count,
                        analyzed_count=current.analyzed_count,
                        lead_count=current.lead_count,
                        contactable_lead_count=current.contactable_lead_count,
                        exported_count=current.exported_count,
                        error_count=current.error_count,
                    )
            await asyncio.sleep(2)
    finally:
        await database.dispose()


if __name__ == "__main__":
    app()
