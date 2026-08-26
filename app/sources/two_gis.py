from typing import Any

import httpx

from app.core.config import Settings
from app.core.enums import ContactsAccess, SourceName
from app.core.errors import ConfigurationError
from app.schemas.domain import SearchCriteria, SourceCompany, SourcePage
from app.sources.http import ResilientHttpClient

TWO_GIS_ITEMS_URL = "https://catalog.api.2gis.com/3.0/items"
TWO_GIS_FIELDS = "items.point,items.rubrics,items.reviews,items.schedule,items.contact_groups"


class TwoGisSource:
    name = SourceName.TWO_GIS

    def __init__(self, settings: Settings) -> None:
        if settings.two_gis_api_key is None:
            raise ConfigurationError("Не задан ключ 2GIS")
        self._key = settings.two_gis_api_key.get_secret_value()
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
        payload = await self._http.request_json(
            "GET",
            TWO_GIS_ITEMS_URL,
            params={
                "q": f"{criteria.query} {criteria.city}",
                "key": self._key,
                "type": "branch",
                "page": page_number,
                "page_size": 50,
                "fields": TWO_GIS_FIELDS,
            },
        )
        result = payload.get("result") or {}
        raw_items = result.get("items", []) if isinstance(result, dict) else []
        items = tuple(
            self._map_company(item, criteria.city)
            for item in raw_items
            if isinstance(item, dict) and item.get("id") and item.get("name")
        )
        total = _optional_int(result.get("total")) if isinstance(result, dict) else None
        has_next = total is not None and page_number * 50 < total
        next_cursor = str(page_number + 1) if has_next else None
        return SourcePage(items=items, next_cursor=next_cursor, exhausted=not has_next)

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


def _optional_text(value: object) -> str | None:
    return str(value).strip() if value is not None and str(value).strip() else None


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_dict(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None
