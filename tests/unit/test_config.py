from pydantic import SecretStr

from app.core.config import Settings
from app.core.enums import SourceName


def test_yandex_requires_explicit_storage_permission(monkeypatch) -> None:
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


def test_sheet_names_are_human_friendly_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.google_sheets_all_companies_worksheet == "Все компании"
    assert settings.google_sheets_qualified_leads_worksheet == "Готовые лиды"
    assert settings.google_sheets_search_runs_worksheet == "Запуски поиска"
