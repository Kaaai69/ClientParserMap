import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.enums import SourceName


def test_yandex_requires_explicit_storage_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    monkeypatch.delenv("TWO_GIS_API_KEY", raising=False)
    monkeypatch.setenv("YANDEX_MAPS_API_KEY", "configured")
    monkeypatch.setenv("YANDEX_STORAGE_ALLOWED", "false")

    settings = Settings(_env_file=None)

    assert settings.enabled_sources == ()


def test_enabled_sources_only_include_configured_and_licensed_adapters() -> None:
    settings = Settings(
        _env_file=None,
        google_places_api_key=SecretStr("google"),
        two_gis_api_key=SecretStr("2gis"),
        yandex_maps_api_key=SecretStr("yandex"),
        yandex_storage_allowed=True,
    )

    assert settings.enabled_sources == (
        SourceName.GOOGLE,
        SourceName.TWO_GIS,
        SourceName.YANDEX,
    )


def test_openstreetmap_requires_explicit_enablement() -> None:
    assert SourceName.OPENSTREETMAP not in Settings(_env_file=None).enabled_sources
    enabled = Settings(_env_file=None, openstreetmap_enabled=True)

    assert SourceName.OPENSTREETMAP in enabled.enabled_sources


def test_sheet_names_are_human_friendly_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.google_sheets_all_companies_worksheet == "Все компании"
    assert settings.google_sheets_qualified_leads_worksheet == "Готовые лиды"
    assert settings.google_sheets_search_runs_worksheet == "Запуски поиска"


def test_empty_service_account_path_does_not_enable_sheets() -> None:
    settings = Settings(
        _env_file=None,
        google_sheets_spreadsheet_id="sheet-id",
        google_service_account_file="",
    )

    assert settings.google_service_account_file is None
    assert settings.sheets_enabled is False
