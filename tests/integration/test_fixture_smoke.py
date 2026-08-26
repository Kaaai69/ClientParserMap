from pathlib import Path

from scripts.fixture_smoke import run_fixture_smoke


def test_fixture_smoke_is_idempotent(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'smoke.sqlite'}"

    first = run_fixture_smoke(database_url=database_url, initialize_schema=True)
    second = run_fixture_smoke(database_url=database_url, initialize_schema=True)

    assert first.unique_count == second.unique_count == 1
    assert first.company_count == second.company_count == 1
    assert second.duplicate_count == 0
