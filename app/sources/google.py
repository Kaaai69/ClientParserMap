from typing import Any

import httpx

from app.core.config import Settings
from app.core.enums import SourceName
from app.core.errors import ConfigurationError
from app.schemas.domain import SearchCriteria, SourceCompany, SourcePage
from app.sources.http import ResilientHttpClient

GOOGLE_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_FIELD_MASK = ",".join(
    (
        "places.id",
        "places.displayName",
        "places.types",
        "places.primaryTypeDisplayName",
        "places.formattedAddress",
        "places.internationalPhoneNumber",
        "places.nationalPhoneNumber",
        "places.websiteUri",
        "places.rating",
        "places.userRatingCount",
        "places.location",
        "places.regularOpeningHours",
        "nextPageToken",
    )
)


class GoogleSource:
    name = SourceName.GOOGLE

    def __init__(self, settings: Settings) -> None:
        if settings.google_places_api_key is None:
            raise ConfigurationError("Не задан ключ Google Places")
        self._key = settings.google_places_api_key.get_secret_value()
        self._http = ResilientHttpClient(
            httpx.AsyncClient(timeout=30),
            max_attempts=settings.source_max_retries,
            backoff_base_seconds=settings.source_backoff_base_seconds,
            requests_per_second=settings.google_requests_per_second,
        )

    async def search_page(
        self,
        criteria: SearchCriteria,
        cursor: str | None,
    ) -> SourcePage:
        body: dict[str, Any] = {
            "textQuery": f"{criteria.query} {criteria.city}",
            "pageSize": 20,
            "languageCode": "ru",
        }
        if cursor:
            body["pageToken"] = cursor
        payload = await self._http.request_json(
            "POST",
            GOOGLE_TEXT_SEARCH_URL,
            headers={
                "X-Goog-Api-Key": self._key,
                "X-Goog-FieldMask": GOOGLE_FIELD_MASK,
            },
            json=body,
        )
        raw_places = payload.get("places", [])
        items = tuple(
            self._map_company(place, criteria.city)
            for place in raw_places
            if isinstance(place, dict) and place.get("id") and place.get("displayName")
        )
        next_cursor = _optional_text(payload.get("nextPageToken"))
        return SourcePage(
            items=items,
            next_cursor=next_cursor,
            exhausted=next_cursor is None,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _map_company(place: dict[str, Any], city: str) -> SourceCompany:
        display_name = place.get("displayName") or {}
        primary_type = place.get("primaryTypeDisplayName") or {}
        location = place.get("location") or {}
        international_phone = _optional_text(place.get("internationalPhoneNumber"))
        national_phone = _optional_text(place.get("nationalPhoneNumber"))
        phones = tuple(
            phone
            for phone in (international_phone, national_phone)
            if phone is not None
        )
        categories = [str(value) for value in place.get("types", []) if value]
        primary_type_text = _optional_text(primary_type.get("text"))
        if primary_type_text:
            categories.insert(0, primary_type_text)
        website = _optional_text(place.get("websiteUri"))
        return SourceCompany(
            source=SourceName.GOOGLE,
            source_id=str(place["id"]),
            name=str(display_name["text"]),
            city=city,
            categories=tuple(dict.fromkeys(categories)),
            address=_optional_text(place.get("formattedAddress")),
            primary_phone=international_phone or national_phone,
            phones=phones,
            websites=(website,) if website else (),
            rating=_optional_float(place.get("rating")),
            reviews_count=_optional_int(place.get("userRatingCount")),
            latitude=_optional_float(location.get("latitude")),
            longitude=_optional_float(location.get("longitude")),
            working_hours=_optional_dict(place.get("regularOpeningHours")),
        )


def _optional_text(value: object) -> str | None:
    return str(value).strip() if value is not None and str(value).strip() else None


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_dict(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None
