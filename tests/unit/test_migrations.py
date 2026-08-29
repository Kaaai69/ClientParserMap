"""Guard the schema against enum drift.

The source columns are VARCHAR sized to the longest enum member, so adding a
source with a longer name silently outgrows the migrated column. SQLite does
not enforce VARCHAR length, so only an explicit width check catches it before
PostgreSQL does.
"""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.command import upgrade
from alembic.config import Config

from app.core.enums import SourceName
from app.db.models import CompanySource, SearchJobCompany, SearchJobSource

SOURCE_COLUMNS = (
    (SearchJobSource, "source"),
    (CompanySource, "source"),
    (SearchJobCompany, "first_source"),
)

MIGRATED_COLUMNS = (
    ("search_job_sources", "source"),
    ("company_sources", "source"),
    ("search_job_companies", "first_source"),
)


def _longest_source_value() -> int:
    return max(len(member.value) for member in SourceName)


def test_model_source_columns_fit_every_source_name() -> None:
    for model, column_name in SOURCE_COLUMNS:
        length = model.__table__.columns[column_name].type.length

        assert length is not None
        assert length >= _longest_source_value(), (
            f"{model.__tablename__}.{column_name} holds {length} chars, "
            f"but SourceName needs {_longest_source_value()}"
        )


def test_migrated_source_columns_fit_every_source_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "migrated.sqlite3"
    # alembic/env.py takes the URL from the settings, not from alembic.ini.
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    upgrade(Config("alembic.ini"), "head")

    engine = sa.create_engine(f"sqlite:///{database}")
    inspector = sa.inspect(engine)
    try:
        for table, column_name in MIGRATED_COLUMNS:
            column = next(
                item for item in inspector.get_columns(table) if item["name"] == column_name
            )
            length = column["type"].length

            assert length is not None
            assert length >= _longest_source_value(), (
                f"migrated {table}.{column_name} holds {length} chars, "
                f"but SourceName needs {_longest_source_value()}"
            )
    finally:
        engine.dispose()
