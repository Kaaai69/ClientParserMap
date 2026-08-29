from typing import Any

import httpx

from app.core.config import Settings
from app.core.enums import SourceName
from app.core.errors import ConfigurationError
from app.schemas.domain import SearchCriteria, SourceCompany, SourcePage
from app.sources.http import ResilientHttpClient

YANDEX_SEARCH_URL = "https://search-maps.yandex.ru/v1/"


class YandexSource:
    """Commercial Yandex search, enabled only with explicit storage permission."""

    name = SourceName.YANDEX

    def __init__(self, settings: Settings) -> None:
        if settings.yandex_maps_api_key is None:
            raise ConfigurationError("Не задан ключ API Яндекс Карт")
        if not settings.yandex_storage_allowed:
            raise ConfigurationError("Хранение данных Яндекс Карт не разрешено текущей лицензией")
        self._key = settings.yandex_maps_api_key.get_secret_value()
        self._http = ResilientHttpClient(
            httpx.AsyncClient(timeout=30),
            max_attempts=settings.source_max_retries,
            backoff_base_seconds=settings.source_backoff_base_seconds,
            requests_per_second=settings.yandex_requests_per_second,
        )

    async def search_page(
        self,
        criteria: SearchCriteria,
        cursor: str | None,
    ) -> SourcePage:
        skip = max(0, int(cursor or "0"))
        payload = await self._http.request_json(
            "GET",
            YANDEX_SEARCH_URL,
            params={
                "apikey": self._key,
                "text": f"{criteria.query} {criteria.city}",
                "type": "biz",
                "lang": "ru_RU",
                "results": 50,
                "skip": skip,
            },
        )
        raw_features = payload.get("features", [])
        items = tuple(
            self._map_company(feature, criteria.city)
            for feature in raw_features
            if isinstance(feature, dict) and _metadata(feature).get("name")
        )
        found = _found_count(payload)
        next_skip = skip + 50
        has_next = found is not None and next_skip < found
        next_cursor = str(next_skip) if has_next else None
        return SourcePage(items=items, next_cursor=next_cursor, exhausted=not has_next)

    async def aclose(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _map_company(feature: dict[str, Any], city: str) -> SourceCompany:
        metadata = _metadata(feature)
        raw_geometry = feature.get("geometry")
        geometry = raw_geometry if isinstance(raw_geometry, dict) else {}
        coordinates = geometry.get("coordinates", [])
        longitude = _coordinate(coordinates, 0)
        latitude = _coordinate(coordinates, 1)
        phones = tuple(
            str(phone["formatted"])
            for phone in metadata.get("Phones", [])
            if isinstance(phone, dict) and phone.get("formatted")
        )
        categories = tuple(
            str(category["name"])
            for category in metadata.get("Categories", [])
            if isinstance(category, dict) and category.get("name")
        )
        raw_rating = metadata.get("Rating")
        rating = raw_rating if isinstance(raw_rating, dict) else {}
        website = _optional_text(metadata.get("url"))
        source_id = metadata.get("id") or feature.get("id")
        return SourceCompany(
            source=SourceName.YANDEX,
            source_id=str(source_id),
            name=str(metadata["name"]),
            city=city,
            categories=categories,
            address=_optional_text(metadata.get("address")),
            primary_phone=phones[0] if phones else None,
            phones=phones,
            websites=(website,) if website else (),
            rating=_optional_float(rating.get("rating")),
            reviews_count=_optional_int(rating.get("reviews")),
            latitude=latitude,
            longitude=longitude,
            working_hours=_optional_dict(metadata.get("Hours")),
        )


def _metadata(feature: dict[str, Any]) -> dict[str, Any]:
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        return {}
    metadata = properties.get("CompanyMetaData")
    return metadata if isinstance(metadata, dict) else {}


def _found_count(payload: dict[str, Any]) -> int | None:
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        return None
    response_metadata = properties.get("ResponseMetaData")
    if not isinstance(response_metadata, dict):
        return None
    search_response = response_metadata.get("SearchResponse")
    if not isinstance(search_response, dict):
        return None
    return _optional_int(search_response.get("found"))


def _coordinate(coordinates: object, index: int) -> float | None:
    if not isinstance(coordinates, list) or len(coordinates) <= index:
        return None
    return _optional_float(coordinates[index])


def _optional_text(value: object) -> str | None:
    return str(value).strip() if value is not None and str(value).strip() else None


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_dict(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None
