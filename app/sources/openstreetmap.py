import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.core.enums import ContactsAccess, SourceName
from app.core.errors import SourceRequestError
from app.schemas.domain import SearchCriteria, SourceCompany, SourcePage
from app.sources.http import ResilientHttpClient

OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_QUERY_TIMEOUT_SECONDS = 60
DETAILING_ALIASES = frozenset(
    {"детейлинг", "автодетейлинг", "детейлинг авто", "detailing", "car detailing"}
)
TEXT_TAGS = ("name", "brand", "operator", "description", "service")


class OpenStreetMapSource:
    name = SourceName.OPENSTREETMAP

    def __init__(self, settings: Settings) -> None:
        self._endpoint = settings.overpass_api_url
        client = httpx.AsyncClient(
            timeout=settings.overpass_timeout_seconds,
            headers={"User-Agent": settings.openstreetmap_user_agent},
        )
        self._http = ResilientHttpClient(
            client,
            max_attempts=settings.source_max_retries,
            backoff_base_seconds=settings.source_backoff_base_seconds,
            requests_per_second=settings.openstreetmap_requests_per_second,
        )

    async def search_page(
        self,
        criteria: SearchCriteria,
        cursor: str | None,
    ) -> SourcePage:
        if cursor is not None:
            return SourcePage(items=(), exhausted=True)
        payload = await self._http.request_json(
            "POST",
            self._endpoint,
            data={"data": _build_query(criteria)},
        )
        _raise_for_remark(payload)
        elements = _payload_elements(payload)
        items = tuple(
            company
            for element in elements
            if (company := _map_company(element, criteria.city)) is not None
        )
        return SourcePage(items=items, exhausted=True)

    async def aclose(self) -> None:
        await self._http.aclose()


def _build_query(criteria: SearchCriteria) -> str:
    """Return the bounded administrative-area Overpass QL query."""
    query = criteria.query.casefold()
    if query in DETAILING_ALIASES:
        # Require a name server-side: the row limit is spent before mapping, and
        # unnamed car washes would otherwise crowd out every usable result.
        selectors = (
            'nwr["amenity"="car_wash"]["name"](area.searchArea);\n'
            'nwr["shop"="car_repair"]["name"](area.searchArea);\n'
        )
    else:
        selectors = _text_selectors(_regex(criteria.query))
    return (
        f"[out:json][timeout:{OVERPASS_QUERY_TIMEOUT_SECONDS}];\n"
        f'area["boundary"="administrative"]["name"={_quoted(criteria.city)}]->.searchArea;\n'
        "(\n"
        f"{selectors}"
        ");\n"
        f"out center tags {criteria.max_results};"
    )


def _quoted(value: str) -> str:
    """Return a JSON-compatible quoted Overpass string."""
    return json.dumps(value, ensure_ascii=False)


def _regex(value: str) -> str:
    """Regex-escape a value and then return its quoted representation."""
    return _quoted(re.escape(value))


def _payload_elements(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the top-level elements list and keep dictionary entries."""
    raw_elements = payload.get("elements")
    if not isinstance(raw_elements, list):
        raise SourceRequestError(
            "SOURCE_INVALID_PAYLOAD",
            "OpenStreetMap вернул ответ неожиданного формата",
            retryable=False,
        )
    return [element for element in raw_elements if isinstance(element, dict)]


def _raise_for_remark(payload: dict[str, Any]) -> None:
    remark = _optional_text(payload.get("remark"))
    if remark:
        raise SourceRequestError(
            "SOURCE_OVERPASS_REMARK",
            "OpenStreetMap не выполнил запрос",
            retryable=True,
        )


def _map_company(element: dict[str, Any], city: str) -> SourceCompany | None:
    """Map one valid named node, way, or relation; skip malformed elements."""
    element_type = element.get("type")
    element_id = element.get("id")
    tags = element.get("tags")
    if (
        element_type not in {"node", "way", "relation"}
        or isinstance(element_id, bool)
        or not isinstance(element_id, int | str)
        or not isinstance(tags, dict)
    ):
        return None
    name = next(
        (
            value
            for key in ("name", "brand", "operator")
            if (value := _optional_text(tags.get(key))) is not None
        ),
        None,
    )
    if name is None:
        return None
    latitude, longitude = _coordinates(element, element_type)
    phones = _tag_values(tags, ("phone", "contact:phone", "mobile", "contact:mobile"))
    opening_hours = _optional_text(tags.get("opening_hours"))
    try:
        return SourceCompany(
            source=SourceName.OPENSTREETMAP,
            source_id=f"{element_type}/{element_id}",
            name=name,
            city=city,
            categories=_categories(tags),
            address=_address(tags),
            primary_phone=phones[0] if phones else None,
            phones=phones,
            emails=_tag_values(tags, ("email", "contact:email")),
            websites=_tag_values(tags, ("website", "contact:website", "url")),
            telegram=_tag_values(tags, ("telegram", "contact:telegram")),
            whatsapp=_tag_values(tags, ("whatsapp", "contact:whatsapp")),
            vk=_tag_values(tags, ("vk", "contact:vk")),
            instagram=_tag_values(tags, ("instagram", "contact:instagram")),
            other_socials=_tag_values(
                tags,
                (
                    "facebook",
                    "contact:facebook",
                    "youtube",
                    "contact:youtube",
                    "x",
                    "contact:x",
                    "twitter",
                    "contact:twitter",
                    "odnoklassniki",
                    "contact:odnoklassniki",
                ),
            ),
            latitude=latitude,
            longitude=longitude,
            working_hours={"opening_hours": opening_hours} if opening_hours else None,
            contacts_access=ContactsAccess.FULL,
        )
    except ValidationError:
        return None


def _tag_values(tags: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, ...]:
    """Split semicolon values and return stable, de-duplicated non-empty text."""
    values: list[str] = []
    for key in keys:
        raw_value = tags.get(key)
        if not isinstance(raw_value, str):
            continue
        for value in raw_value.split(";"):
            text = value.strip()
            if text and text not in values:
                values.append(text)
    return tuple(values)


def _text_selectors(pattern: str) -> str:
    return "".join(f"nwr[{_quoted(tag)}~{pattern},i](area.searchArea);\n" for tag in TEXT_TAGS)


def _categories(tags: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        f"{key}={value}"
        for key in ("amenity", "shop", "craft", "office", "tourism", "healthcare")
        if (value := _optional_text(tags.get(key))) is not None
    )


def _address(tags: dict[str, Any]) -> str | None:
    if full_address := _optional_text(tags.get("addr:full")):
        return full_address
    return (
        ", ".join(
            value
            for key in ("addr:street", "addr:housenumber")
            if (value := _optional_text(tags.get(key))) is not None
        )
        or None
    )


def _coordinates(element: dict[str, Any], element_type: str) -> tuple[float | None, float | None]:
    raw_coordinates = element if element_type == "node" else element.get("center")
    coordinates = raw_coordinates if isinstance(raw_coordinates, dict) else {}
    latitude = _optional_float(coordinates.get("lat"))
    longitude = _optional_float(coordinates.get("lon"))
    return (latitude, longitude) if latitude is not None and longitude is not None else (None, None)


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
