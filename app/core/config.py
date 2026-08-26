from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.enums import SourceName


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    log_level: str = "INFO"
    api_auth_key: SecretStr | None = None

    database_url: str = (
        "postgresql+asyncpg://client_parser:client_parser@localhost:5432/client_parser"
    )
    redis_url: str = "redis://localhost:6379/0"
    rq_queue_name: str = "searches"
    rq_job_timeout_seconds: int = Field(default=7200, ge=60)

    google_places_api_key: SecretStr | None = None
    two_gis_api_key: SecretStr | None = None
    yandex_maps_api_key: SecretStr | None = None
    yandex_storage_allowed: bool = False

    google_sheets_spreadsheet_id: str | None = None
    google_sheets_all_companies_worksheet: str = "Все компании"
    google_sheets_qualified_leads_worksheet: str = "Готовые лиды"
    google_sheets_search_runs_worksheet: str = "Запуски поиска"
    google_service_account_file: Path | None = None

    lead_score_threshold: int = Field(default=50, ge=0, le=100)
    scoring_rules_file: Path = Path("app/scoring/scoring_rules.toml")

    website_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    max_concurrent_website_checks: int = Field(default=10, ge=1, le=100)
    max_website_redirects: int = Field(default=5, ge=0, le=20)
    max_html_bytes: int = Field(default=5_000_000, ge=1024, le=25_000_000)
    max_contact_pages: int = Field(default=8, ge=1, le=20)
    website_check_ttl_hours: int = Field(default=168, ge=1)

    source_max_retries: int = Field(default=3, ge=1, le=10)
    source_backoff_base_seconds: float = Field(default=1.0, ge=0, le=60)
    google_requests_per_second: float = Field(default=5.0, gt=0, le=100)
    two_gis_requests_per_second: float = Field(default=5.0, gt=0, le=100)
    yandex_requests_per_second: float = Field(default=1.0, gt=0, le=100)

    @model_validator(mode="after")
    def normalize_optional_strings(self) -> Self:
        for field_name in (
            "google_sheets_spreadsheet_id",
            "google_places_api_key",
            "two_gis_api_key",
            "yandex_maps_api_key",
            "api_auth_key",
        ):
            value = getattr(self, field_name)
            if isinstance(value, str) and not value.strip():
                setattr(self, field_name, None)
            if isinstance(value, SecretStr) and not value.get_secret_value().strip():
                setattr(self, field_name, None)
        return self

    @property
    def enabled_sources(self) -> tuple[SourceName, ...]:
        enabled: list[SourceName] = []
        if self.google_places_api_key:
            enabled.append(SourceName.GOOGLE)
        if self.two_gis_api_key:
            enabled.append(SourceName.TWO_GIS)
        if self.yandex_maps_api_key and self.yandex_storage_allowed:
            enabled.append(SourceName.YANDEX)
        return tuple(enabled)

    @property
    def sheets_enabled(self) -> bool:
        return bool(self.google_sheets_spreadsheet_id and self.google_service_account_file)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
