import pytest
from typer.testing import CliRunner

from app.cli import CliSearchResult, app


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
