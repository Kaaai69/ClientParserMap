from pydantic import SecretStr

from app.core.config import Settings
from app.core.enums import SourceName
from app.sources.registry import build_source_registry


def test_registry_never_enables_unlicensed_yandex() -> None:
    settings = Settings(
        _env_file=None,
        yandex_maps_api_key=SecretStr("yandex-key"),
        yandex_storage_allowed=False,
    )

    assert SourceName.YANDEX not in build_source_registry(settings)


def test_registry_builds_only_configured_sources() -> None:
    settings = Settings(
        _env_file=None,
        google_places_api_key=SecretStr("google-key"),
        two_gis_api_key=SecretStr("2gis-key"),
    )

    assert set(build_source_registry(settings)) == {SourceName.GOOGLE, SourceName.TWO_GIS}


def test_registry_builds_keyless_openstreetmap_when_enabled() -> None:
    settings = Settings(_env_file=None, openstreetmap_enabled=True)

    assert set(build_source_registry(settings)) == {SourceName.OPENSTREETMAP}
