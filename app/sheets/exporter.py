from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

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
from app.core.errors import ConfigurationError
from app.sheets.client import SheetRow, SheetsClient
from app.sheets.columns import (
    ALL_COMPANIES_COLUMNS,
    MANUAL_LEAD_COLUMNS,
    QUALIFIED_LEADS_COLUMNS,
    SEARCH_RUNS_COLUMNS,
)


class SheetModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CompanySheetRecord(SheetModel):
    id: int
    discovered_at: datetime
    updated_at: datetime
    name: str
    search_query: str
    category: str | None = None
    city: str
    address: str | None = None
    primary_phone: str | None = None
    additional_phones: tuple[str, ...] = ()
    whatsapp: tuple[str, ...] = ()
    telegram: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    vk: tuple[str, ...] = ()
    instagram: tuple[str, ...] = ()
    other_socials: tuple[str, ...] = ()
    website: str | None = None
    primary_source: SourceName | None = None
    sources: tuple[SourceName, ...] = ()
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews_count: int | None = Field(default=None, ge=0)
    website_status: WebsiteStatus
    cms: CMS
    website_type: WebsiteType
    https_enabled: bool = False
    site_opportunity_score: int = Field(ge=0, le=100)
    contactability_score: int = Field(ge=0, le=100)
    contacts_found: bool
    preferred_contact_type: ContactType | None = None
    preferred_contact_value: str | None = None
    reasons: tuple[str, ...] = ()
    lead_state: LeadState


class SearchJobSheetRecord(SheetModel):
    id: int
    created_at: datetime
    finished_at: datetime | None = None
    city: str
    query: str
    sources: tuple[SourceName, ...]
    min_rating: float | None = None
    min_reviews: int | None = None
    max_results: int
    status: JobStatus
    stage: JobStage
    found_count: int = 0
    filtered_out_count: int = 0
    unique_count: int = 0
    analyzed_count: int = 0
    lead_count: int = 0
    contactable_lead_count: int = 0
    exported_count: int = 0
    error_count: int = 0


@dataclass(frozen=True, slots=True)
class SheetWriteResult:
    worksheet_name: str
    entity_type: str
    entity_id: int
    row_number: int
    created: bool


class SheetsExporter:
    def __init__(self, client: SheetsClient, settings: Settings) -> None:
        if not settings.google_sheets_spreadsheet_id:
            raise ConfigurationError("Не задан ID Google-таблицы")
        self._client = client
        self._all_companies_name = settings.google_sheets_all_companies_worksheet
        self._qualified_name = settings.google_sheets_qualified_leads_worksheet
        self._runs_name = settings.google_sheets_search_runs_worksheet
        self._indexes: dict[str, dict[str, SheetRow]] = {}

    async def sync_company(
        self,
        company: CompanySheetRecord,
    ) -> tuple[SheetWriteResult, ...]:
        results = [
            await self._upsert(
                self._all_companies_name,
                ALL_COMPANIES_COLUMNS,
                "ID компании",
                _all_company_values(company),
                entity_type="company",
                entity_id=company.id,
            )
        ]
        if company.lead_state is LeadState.QUALIFIED:
            results.append(
                await self._upsert(
                    self._qualified_name,
                    QUALIFIED_LEADS_COLUMNS,
                    "ID компании",
                    _qualified_values(company),
                    entity_type="company",
                    entity_id=company.id,
                    manual_columns=MANUAL_LEAD_COLUMNS,
                )
            )
        return tuple(results)

    async def sync_job(self, job: SearchJobSheetRecord) -> SheetWriteResult:
        return await self._upsert(
            self._runs_name,
            SEARCH_RUNS_COLUMNS,
            "ID запуска",
            _job_values(job),
            entity_type="search_job",
            entity_id=job.id,
        )

    async def _upsert(
        self,
        worksheet_name: str,
        headers: Sequence[str],
        id_column: str,
        values: dict[str, str],
        *,
        entity_type: str,
        entity_id: int,
        manual_columns: frozenset[str] = frozenset(),
    ) -> SheetWriteResult:
        index = await self._worksheet_index(worksheet_name, headers, id_column)
        id_value = values[id_column]
        existing = index.get(id_value)
        if existing is None:
            row_number = await self._client.append_row(worksheet_name, headers, values)
            index[id_value] = SheetRow(row_number=row_number, values=dict(values))
            return SheetWriteResult(
                worksheet_name,
                entity_type,
                entity_id,
                row_number,
                created=True,
            )
        if manual_columns:
            existing = await self._client.read_row(
                worksheet_name,
                existing.row_number,
                headers,
            )
        merged = _preserve_manual_values(values, existing, manual_columns)
        await self._client.update_row(worksheet_name, existing.row_number, headers, merged)
        index[id_value] = SheetRow(row_number=existing.row_number, values=dict(merged))
        return SheetWriteResult(
            worksheet_name,
            entity_type,
            entity_id,
            existing.row_number,
            created=False,
        )

    async def _worksheet_index(
        self,
        worksheet_name: str,
        headers: Sequence[str],
        id_column: str,
    ) -> dict[str, SheetRow]:
        existing_index = self._indexes.get(worksheet_name)
        if existing_index is not None:
            return existing_index
        await self._client.ensure_worksheet(worksheet_name, headers)
        rows = await self._client.read_records(worksheet_name)
        index = {row.values[id_column]: row for row in rows if row.values.get(id_column)}
        self._indexes[worksheet_name] = index
        return index


def _all_company_values(company: CompanySheetRecord) -> dict[str, str]:
    return {
        "Дата обнаружения": _datetime(company.discovered_at),
        "Дата обновления": _datetime(company.updated_at),
        "Название": company.name,
        "Поисковый запрос": company.search_query,
        "Категория": company.category or "",
        "Город": company.city,
        "Адрес": company.address or "",
        "Основной телефон": company.primary_phone or "",
        "Дополнительные телефоны": _join(company.additional_phones),
        "WhatsApp": _join(company.whatsapp),
        "Telegram": _join(company.telegram),
        "Email": _join(company.emails),
        "VK": _join(company.vk),
        "Instagram": _join(company.instagram),
        "Другие соцсети": _join(company.other_socials),
        "Сайт": company.website or "",
        "Основной источник": _enum(company.primary_source),
        "Все источники": _join(tuple(source.value for source in company.sources)),
        "Рейтинг": _number(company.rating),
        "Количество отзывов": _number(company.reviews_count),
        "Статус сайта": company.website_status.value,
        "CMS / конструктор": company.cms.value,
        "Тип сайта": company.website_type.value,
        "HTTPS": _yes_no(company.https_enabled),
        "Site Opportunity Score": str(company.site_opportunity_score),
        "Contactability Score": str(company.contactability_score),
        "Контакты найдены": _yes_no(company.contacts_found),
        "Предпочтительный способ связи": _enum(company.preferred_contact_type),
        "Предпочтительный контакт": company.preferred_contact_value or "",
        "Причина оценки": _join(company.reasons),
        "Статус лида": company.lead_state.value,
        "ID компании": str(company.id),
    }


def _qualified_values(company: CompanySheetRecord) -> dict[str, str]:
    all_values = _all_company_values(company)
    return {
        "Дата добавления": _datetime(company.discovered_at),
        "Название": company.name,
        "Категория": company.category or "",
        "Город": company.city,
        "Адрес": company.address or "",
        "Основной телефон": company.primary_phone or "",
        "Дополнительные телефоны": _join(company.additional_phones),
        "WhatsApp": _join(company.whatsapp),
        "Telegram": _join(company.telegram),
        "Email": _join(company.emails),
        "VK": _join(company.vk),
        "Instagram": _join(company.instagram),
        "Сайт": company.website or "",
        "Источники": all_values["Все источники"],
        "Рейтинг": all_values["Рейтинг"],
        "Количество отзывов": all_values["Количество отзывов"],
        "Статус сайта": company.website_status.value,
        "CMS / конструктор": company.cms.value,
        "HTTPS": _yes_no(company.https_enabled),
        "Site Opportunity Score": str(company.site_opportunity_score),
        "Contactability Score": str(company.contactability_score),
        "Причина попадания": _join(company.reasons),
        "Предпочтительный способ связи": _enum(company.preferred_contact_type),
        "Предпочтительный контакт": company.preferred_contact_value or "",
        "Статус работы": "Новый",
        "Менеджер": "",
        "Комментарий": "",
        "ID компании": str(company.id),
    }


def _job_values(job: SearchJobSheetRecord) -> dict[str, str]:
    return {
        "Дата запуска": _datetime(job.created_at),
        "Дата завершения": _datetime(job.finished_at),
        "Город": job.city,
        "Поисковый запрос": job.query,
        "Использованные источники": _join(tuple(source.value for source in job.sources)),
        "Минимальный рейтинг": _number(job.min_rating),
        "Минимальное количество отзывов": _number(job.min_reviews),
        "Лимит результатов": str(job.max_results),
        "Статус запуска": job.status.value,
        "Текущий этап": job.stage.value,
        "Всего найдено": str(job.found_count),
        "Отфильтровано": str(job.filtered_out_count),
        "Уникальных компаний": str(job.unique_count),
        "Проверено сайтов": str(job.analyzed_count),
        "Потенциальных лидов": str(job.lead_count),
        "Лидов с контактами": str(job.contactable_lead_count),
        "Записано в Google Sheets": str(job.exported_count),
        "Количество ошибок": str(job.error_count),
        "ID запуска": str(job.id),
    }


def _preserve_manual_values(
    values: dict[str, str],
    existing: SheetRow,
    manual_columns: frozenset[str],
) -> dict[str, str]:
    merged = dict(values)
    for column in manual_columns:
        merged[column] = existing.values.get(column, values.get(column, ""))
    return merged


def _join(values: Sequence[str]) -> str:
    return ", ".join(value for value in values if value)


def _datetime(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value is not None else ""


def _number(value: float | int | None) -> str:
    return f"{value:g}" if value is not None else ""


def _yes_no(value: bool) -> str:
    return "Да" if value else "Нет"


def _enum(value: SourceName | ContactType | None) -> str:
    return value.value if value is not None else ""
