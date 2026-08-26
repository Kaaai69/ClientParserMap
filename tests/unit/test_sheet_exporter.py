from collections.abc import Sequence
from datetime import UTC, datetime

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
from app.sheets.client import SheetRow
from app.sheets.columns import ALL_COMPANIES_COLUMNS, QUALIFIED_LEADS_COLUMNS
from app.sheets.exporter import CompanySheetRecord, SearchJobSheetRecord, SheetsExporter


class InMemorySheetsClient:
    def __init__(self) -> None:
        self.headers: dict[str, tuple[str, ...]] = {}
        self.data: dict[str, list[dict[str, str]]] = {}
        self.read_counts: dict[str, int] = {}

    async def ensure_worksheet(self, name: str, headers: Sequence[str]) -> None:
        self.headers[name] = tuple(headers)
        self.data.setdefault(name, [])

    async def read_records(self, name: str) -> list[SheetRow]:
        self.read_counts[name] = self.read_counts.get(name, 0) + 1
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
        self.data.setdefault(name, []).append(dict(values))
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

    def row(self, name: str, key: str, value: str) -> dict[str, str] | None:
        return next((row for row in self.rows(name) if row.get(key) == value), None)

    def edit(self, name: str, entity_id: str, changes: dict[str, str]) -> None:
        row = self.row(name, "ID компании", entity_id)
        assert row is not None
        row.update(changes)


def settings() -> Settings:
    return Settings(_env_file=None, google_sheets_spreadsheet_id="sheet-1")


def company(*, qualified: bool = False, name: str = "Nyra Auto") -> CompanySheetRecord:
    now = datetime(2026, 8, 26, 10, 30, tzinfo=UTC)
    return CompanySheetRecord(
        id=42,
        discovered_at=now,
        updated_at=now,
        name=name,
        search_query="детейлинг",
        category="Детейлинг",
        city="Москва",
        address="Тверская улица, 10",
        primary_phone="+79991234567",
        emails=("sales@example.ru",),
        website="https://example.ru/",
        primary_source=SourceName.GOOGLE,
        sources=(SourceName.GOOGLE, SourceName.TWO_GIS),
        rating=4.9,
        reviews_count=137,
        website_status=WebsiteStatus.NO_WEBSITE,
        cms=CMS.CUSTOM_OR_UNKNOWN,
        website_type=WebsiteType.NORMAL,
        https_enabled=True,
        site_opportunity_score=100 if qualified else 20,
        contactability_score=100,
        contacts_found=True,
        preferred_contact_type=ContactType.PHONE,
        preferred_contact_value="+79991234567",
        reasons=("Нет собственного сайта",),
        lead_state=LeadState.QUALIFIED if qualified else LeadState.BELOW_THRESHOLD,
    )


async def test_every_company_is_written_to_all_companies() -> None:
    client = InMemorySheetsClient()

    await SheetsExporter(client, settings()).sync_company(company())

    assert client.row("Все компании", "ID компании", "42") is not None
    assert client.rows("Готовые лиды") == []
    assert client.headers["Все компании"] == ALL_COMPANIES_COLUMNS


async def test_qualified_company_is_written_to_both_company_worksheets() -> None:
    client = InMemorySheetsClient()

    await SheetsExporter(client, settings()).sync_company(company(qualified=True))

    assert len(client.rows("Все компании")) == 1
    assert len(client.rows("Готовые лиды")) == 1
    assert client.headers["Готовые лиды"] == QUALIFIED_LEADS_COLUMNS


async def test_update_preserves_manual_lead_columns() -> None:
    client = InMemorySheetsClient()
    exporter = SheetsExporter(client, settings())
    await exporter.sync_company(company(qualified=True))
    client.edit(
        "Готовые лиды",
        "42",
        {"Статус работы": "В работе", "Менеджер": "Анна", "Комментарий": "Позвонить"},
    )

    await exporter.sync_company(company(qualified=True, name="Nyra Auto Updated"))

    row = client.row("Готовые лиды", "ID компании", "42")
    assert row is not None
    assert row["Название"] == "Nyra Auto Updated"
    assert (row["Статус работы"], row["Менеджер"], row["Комментарий"]) == (
        "В работе",
        "Анна",
        "Позвонить",
    )
    assert len(client.rows("Готовые лиды")) == 1


async def test_job_row_is_idempotently_updated() -> None:
    client = InMemorySheetsClient()
    exporter = SheetsExporter(client, settings())
    started = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
    initial = SearchJobSheetRecord(
        id=7,
        created_at=started,
        city="Москва",
        query="детейлинг",
        sources=(SourceName.GOOGLE,),
        max_results=300,
        status=JobStatus.RUNNING,
        stage=JobStage.COLLECTING,
    )
    await exporter.sync_job(initial)

    await exporter.sync_job(
        initial.model_copy(
            update={
                "status": JobStatus.COMPLETED,
                "stage": JobStage.FINISHED,
                "found_count": 10,
            }
        )
    )

    assert len(client.rows("Запуски поиска")) == 1
    assert client.rows("Запуски поиска")[0]["Статус запуска"] == "COMPLETED"
    assert client.rows("Запуски поиска")[0]["Всего найдено"] == "10"


async def test_exporter_builds_each_worksheet_index_once() -> None:
    client = InMemorySheetsClient()
    exporter = SheetsExporter(client, settings())

    await exporter.sync_company(company(qualified=True))
    await exporter.sync_company(
        company(qualified=True, name="Second").model_copy(update={"id": 43})
    )

    assert client.read_counts["Все компании"] == 1
    assert client.read_counts["Готовые лиды"] == 1


async def test_no_contacts_company_never_enters_qualified_sheet() -> None:
    client = InMemorySheetsClient()
    record = company(qualified=True).model_copy(
        update={
            "contacts_found": False,
            "contactability_score": 0,
            "preferred_contact_type": None,
            "preferred_contact_value": None,
            "lead_state": LeadState.NO_CONTACTS,
        }
    )

    await SheetsExporter(client, settings()).sync_company(record)

    assert len(client.rows("Все компании")) == 1
    assert client.rows("Готовые лиды") == []
