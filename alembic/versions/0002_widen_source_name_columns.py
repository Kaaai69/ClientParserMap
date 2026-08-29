"""widen source name columns for openstreetmap

The source enums are stored as VARCHAR sized to the longest member. The initial
migration predates the OpenStreetMap source, so those columns are VARCHAR(6) —
wide enough for "google", "2gis" and "yandex", but not for "openstreetmap".
Widen them to match the current enum.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-29 12:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("search_job_sources", "source", "source_name"),
    ("company_sources", "source", "company_source_name"),
    ("search_job_companies", "first_source", "first_discovery_source"),
)

NULLABLE = {("search_job_companies", "first_source")}

OLD_VALUES = ("google", "2gis", "yandex")
NEW_VALUES = ("google", "2gis", "yandex", "openstreetmap")


def _alter(values: tuple[str, ...], existing: tuple[str, ...]) -> None:
    # Batch mode so the migration also applies on SQLite, which cannot
    # ALTER COLUMN TYPE in place.
    for table, column, enum_name in COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column,
                type_=sa.Enum(*values, name=enum_name, native_enum=False),
                existing_type=sa.Enum(*existing, name=enum_name, native_enum=False),
                existing_nullable=(table, column) in NULLABLE,
            )


def upgrade() -> None:
    _alter(NEW_VALUES, OLD_VALUES)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM search_job_sources WHERE source = 'openstreetmap'"))
    op.execute(sa.text("DELETE FROM company_sources WHERE source = 'openstreetmap'"))
    op.execute(
        sa.text(
            "UPDATE search_job_companies SET first_source = NULL "
            "WHERE first_source = 'openstreetmap'"
        )
    )
    _alter(OLD_VALUES, NEW_VALUES)
