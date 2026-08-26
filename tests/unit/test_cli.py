from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from app.cli import CliSearchResult, app, execute_search
from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.db.base import Base
from app.db.session import Database


def test_cli_prints_completed_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute_search(**kwargs: object) -> CliSearchResult:
        return CliSearchResult(
            job_id=7,
            status="COMPLETED",
            found_count=2,
            unique_count=1,
            analyzed_count=1,
            lead_count=1,
            contactable_lead_count=1,
            exported_count=2,
            error_count=0,
        )

    monkeypatch.setattr("app.cli.execute_search", fake_execute_search)

    result = CliRunner().invoke(
        app,
        ["search", "--city", "Москва", "--query", "детейлинг"],
    )

    assert result.exit_code == 0
    assert "Unique: 1" in result.stdout
    assert "Exported to Google Sheets: 2" in result.stdout


def test_cli_no_wait_prints_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute_search(**kwargs: object) -> CliSearchResult:
        return CliSearchResult(job_id=9, status="PENDING")

    monkeypatch.setattr("app.cli.execute_search", fake_execute_search)

    result = CliRunner().invoke(
        app,
        ["search", "--city", "Москва", "--query", "детейлинг", "--no-wait"],
    )

    assert result.exit_code == 0
    assert "Search job: 9 (PENDING)" in result.stdout


async def test_cli_reports_queue_publication_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'cli.sqlite'}"
    database = Database(database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await database.dispose()

    class UnavailableQueue:
        async def enqueue_search(
            self,
            job_id: int,
            deterministic_id: str,
            job_timeout: int,
        ) -> None:
            raise ConnectionError("redis unavailable")

    def unavailable_queue(settings: Settings) -> UnavailableQueue:
        return UnavailableQueue()

    monkeypatch.setattr("app.cli.RQSearchQueue.from_settings", unavailable_queue)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        google_places_api_key=SecretStr("google-key"),
    )

    with pytest.raises(ConfigurationError, match="Redis"):
        await execute_search(
            city="Москва",
            query="детейлинг",
            min_rating=None,
            min_reviews=None,
            max_results=10,
            sources=None,
            no_wait=True,
            settings=settings,
        )
