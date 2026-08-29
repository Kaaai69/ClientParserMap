from app.core.config import Settings
from app.core.enums import SourceName
from app.sources.base import LeadSource
from app.sources.google import GoogleSource
from app.sources.openstreetmap import OpenStreetMapSource
from app.sources.two_gis import TwoGisSource
from app.sources.yandex import YandexSource


def build_source_registry(settings: Settings) -> dict[SourceName, LeadSource]:
    sources: dict[SourceName, LeadSource] = {}
    if settings.google_places_api_key is not None:
        sources[SourceName.GOOGLE] = GoogleSource(settings)
    if settings.two_gis_api_key is not None:
        sources[SourceName.TWO_GIS] = TwoGisSource(settings)
    if settings.yandex_maps_api_key is not None and settings.yandex_storage_allowed:
        sources[SourceName.YANDEX] = YandexSource(settings)
    if settings.openstreetmap_enabled:
        sources[SourceName.OPENSTREETMAP] = OpenStreetMapSource(settings)
    return sources
