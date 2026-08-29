from typing import Any

import httpx

from app.core.config import Settings
from app.core.enums import ContactsAccess, SourceName
from app.core.errors import ConfigurationError, SourceRequestError
from app.schemas.domain import SearchCriteria, SourceCompany, SourcePage
from app.sources.http import ResilientHttpClient

TWO_GIS_ITEMS_URL = "https://catalog.api.2gis.com/3.0/items"
TWO_GIS_REGION_SEARCH_URL = "https://catalog.api.2gis.com/2.0/region/search"
TWO_GIS_FIELDS = "items.point,items.rubrics,items.reviews,items.schedule,items.contact_groups"
TWO_GIS_PAGE_SIZE = 10


class TwoGisSource:
    name = SourceName.TWO_GIS

    def __init__(self, settings: Settings) -> None:
        if settings.two_gis_api_key is None:
            raise ConfigurationError("Не задан ключ 2GIS")
        self._key = settings.two_gis_api_key.get_secret_value()
        self._region_ids: dict[str, str] = {}
        self._http = ResilientHttpClient(
            httpx.AsyncClient(timeout=30),
            max_attempts=settings.source_max_retries,
            backoff_base_seconds=settings.source_backoff_base_seconds,
            requests_per_second=settings.two_gis_requests_per_second,
        )

    async def search_page(
        self,
        criteria: SearchCriteria,
        cursor: str | None,
    ) -> SourcePage:
        page_number = max(1, int(cursor or "1"))
        region_id = await self._resolve_region_id(criteria.city)
        payload = await self._http.request_json(
            "GET",
            TWO_GIS_ITEMS_URL,
            params={
                "q": criteria.query,
                "region_id": region_id,
                "key": self._key,
                "type": "branch",
                "page": page_number,
                "page_size": TWO_GIS_PAGE_SIZE,
                "fields": TWO_GIS_FIELDS,
            },
        )
        result = _api_result(payload)
        raw_items = result.get("items", [])
        items = tuple(
            self._map_company(item, criteria.city)
            for item in raw_items
            if isinstance(item, dict) and item.get("id") and item.get("name")
        )
        total = _optional_int(result.get("total"))
        has_next = total is not None and page_number * TWO_GIS_PAGE_SIZE < total
        next_cursor = str(page_number + 1) if has_next else None
        return SourcePage(items=items, next_cursor=next_cursor, exhausted=not has_next)

    async def _resolve_region_id(self, city: str) -> str:
        cache_key = city.casefold()
        if cached := self._region_ids.get(cache_key):
            return cached
        payload = await self._http.request_json(
            "GET",
            TWO_GIS_REGION_SEARCH_URL,
            params={"q": city, "key": self._key},
        )
        result = _api_result(payload)
        items = _region_entries(result)
        matching = next(
            (region_id for region_id, name in items if name.casefold() == cache_key),
            None,
        )
        if matching is None and items:
            matching = items[0][0]
        if matching is None:
            raise SourceRequestError(
                "SOURCE_REGION_NOT_FOUND",
                "2GIS не нашёл указанный город",
                retryable=False,
            )
        self._region_ids[cache_key] = matching
        return matching

    async def aclose(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _map_company(item: dict[str, Any], city: str) -> SourceCompany:
        contacts = _group_contacts(item.get("contact_groups"))
        raw_point = item.get("point")
        point = raw_point if isinstance(raw_point, dict) else {}
        raw_reviews = item.get("reviews")
        reviews = raw_reviews if isinstance(raw_reviews, dict) else {}
        raw_rubrics = item.get("rubrics")
        rubrics = raw_rubrics if isinstance(raw_rubrics, list) else []
        categories = tuple(
            str(rubric["name"])
            for rubric in rubrics
            if isinstance(rubric, dict) and rubric.get("name")
        )
        phones = contacts.get("phone", ())
        return SourceCompany(
            source=SourceName.TWO_GIS,
            source_id=str(item["id"]),
            name=str(item["name"]),
            city=city,
            categories=categories,
            address=_optional_text(item.get("address_name")),
            primary_phone=phones[0] if phones else None,
            phones=phones,
            emails=contacts.get("email", ()),
            websites=contacts.get("website", ()),
            telegram=contacts.get("telegram", ()),
            whatsapp=contacts.get("whatsapp", ()),
            vk=contacts.get("vkontakte", ()),
            instagram=contacts.get("instagram", ()),
            other_socials=contacts.get("other", ()),
            rating=_optional_float(reviews.get("rating")),
            reviews_count=_optional_int(reviews.get("review_count")),
            latitude=_optional_float(point.get("lat")),
            longitude=_optional_float(point.get("lon")),
            working_hours=_optional_dict(item.get("schedule")),
            contacts_access=(
                ContactsAccess.FULL if "contact_groups" in item else ContactsAccess.LIMITED
            ),
        )


def _group_contacts(raw_groups: object) -> dict[str, tuple[str, ...]]:
    collected: dict[str, list[str]] = {}
    if not isinstance(raw_groups, list):
        return {}
    for group in raw_groups:
        if not isinstance(group, dict) or not isinstance(group.get("contacts"), list):
            continue
        for contact in group["contacts"]:
            if not isinstance(contact, dict):
                continue
            contact_type = str(contact.get("type", "other")).lower()
            value = _optional_text(contact.get("url")) or _optional_text(contact.get("value"))
            if not value:
                continue
            bucket = collected.setdefault(contact_type, [])
            if value not in bucket:
                bucket.append(value)
    return {key: tuple(values) for key, values in collected.items()}


def _api_result(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("meta")
    code = _optional_int(meta.get("code")) if isinstance(meta, dict) else None
    if code != 200:
        if code is None:
            raise SourceRequestError(
                "SOURCE_INVALID_PAYLOAD",
                "2GIS вернул ответ неожиданного формата",
                retryable=False,
            )
        raise SourceRequestError(
            f"SOURCE_API_{code}",
            "2GIS отклонил запрос",
            retryable=code in {429, 500, 502, 503, 504},
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise SourceRequestError(
            "SOURCE_INVALID_PAYLOAD",
            "2GIS вернул ответ неожиданного формата",
            retryable=False,
        )
    return result


def _region_entries(result: dict[str, Any]) -> list[tuple[str, str]]:
    raw_items = result.get("items")
    if not isinstance(raw_items, list):
        raise SourceRequestError(
            "SOURCE_INVALID_PAYLOAD",
            "2GIS вернул ответ неожиданного формата",
            retryable=False,
        )
    entries: list[tuple[str, str]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise SourceRequestError(
                "SOURCE_INVALID_PAYLOAD",
                "2GIS вернул ответ неожиданного формата",
                retryable=False,
            )
        raw_id = raw_item.get("id")
        raw_name = raw_item.get("name")
        if (
            not isinstance(raw_id, str)
            or not raw_id.strip()
            or not isinstance(raw_name, str)
            or not raw_name.strip()
        ):
            raise SourceRequestError(
                "SOURCE_INVALID_PAYLOAD",
                "2GIS вернул ответ неожиданного формата",
                retryable=False,
            )
        entries.append((raw_id.strip(), raw_name.strip()))
    return entries


def _optional_text(value: object) -> str | None:
    return str(value).strip() if value is not None and str(value).strip() else None


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_dict(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None
