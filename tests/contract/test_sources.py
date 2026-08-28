from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from respx import MockRouter

from app.core.config import Settings
from app.core.enums import ContactsAccess, SourceName
from app.core.errors import SourceRequestError
from app.schemas.domain import SearchCriteria
from app.sources.google import GoogleSource
from app.sources.http import ResilientHttpClient
from app.sources.two_gis import TwoGisSource
from app.sources.yandex import YandexSource


def criteria() -> SearchCriteria:
    return SearchCriteria(city="Москва", query="детейлинг", max_results=300)


async def test_google_maps_enterprise_fields_and_next_token(
    respx_mock: MockRouter,
    fixture_json: Callable[[str], dict[str, Any]],
) -> None:
    route = respx_mock.post("https://places.googleapis.com/v1/places:searchText").respond(
        json=fixture_json("google_places_page.json")
    )
    source = GoogleSource(Settings(_env_file=None, google_places_api_key=SecretStr("google-key")))

    page = await source.search_page(criteria(), None)

    assert page.next_cursor == "google-next"
    assert page.items[0].phones == ("+7 999 123-45-67", "8 (999) 123-45-67")
    assert page.items[0].primary_phone == "+7 999 123-45-67"
    assert page.items[0].reviews_count == 137
    assert "places.internationalPhoneNumber" in route.calls[0].request.headers["X-Goog-FieldMask"]


async def test_two_gis_maps_contacts_and_next_page(
    respx_mock: MockRouter,
    fixture_json: Callable[[str], dict[str, Any]],
) -> None:
    region_route = respx_mock.get("https://catalog.api.2gis.com/2.0/region/search").respond(
        json={
            "meta": {"code": 200},
            "result": {"items": [{"id": "32", "name": "Москва", "type": "region"}]},
        }
    )
    items_route = respx_mock.get("https://catalog.api.2gis.com/3.0/items").respond(
        json=fixture_json("two_gis_page.json")
    )
    source = TwoGisSource(Settings(_env_file=None, two_gis_api_key=SecretStr("2gis-key")))

    page = await source.search_page(criteria(), None)
    await source.search_page(criteria(), "2")

    company = page.items[0]
    assert page.next_cursor == "2"
    assert company.contacts_access is ContactsAccess.FULL
    assert company.emails == ("sales@nyra-auto.example",)
    assert company.telegram == ("https://t.me/nyra_auto",)
    assert company.whatsapp == ("https://wa.me/79991234567",)
    assert region_route.call_count == 1
    assert region_route.calls[0].request.url.params["q"] == "Москва"
    assert items_route.calls[0].request.url.params["q"] == "детейлинг"
    assert items_route.calls[0].request.url.params["region_id"] == "32"
    assert items_route.calls[0].request.url.params["page_size"] == "10"


async def test_two_gis_missing_contact_permission_is_limited(
    respx_mock: MockRouter,
    fixture_json: Callable[[str], dict[str, Any]],
) -> None:
    payload = fixture_json("two_gis_page.json")
    payload["result"]["items"][0].pop("contact_groups")
    respx_mock.get("https://catalog.api.2gis.com/2.0/region/search").respond(
        json={
            "meta": {"code": 200},
            "result": {"items": [{"id": "32", "name": "Москва", "type": "region"}]},
        }
    )
    respx_mock.get("https://catalog.api.2gis.com/3.0/items").respond(json=payload)
    source = TwoGisSource(Settings(_env_file=None, two_gis_api_key=SecretStr("2gis-key")))

    page = await source.search_page(criteria(), None)

    assert page.items[0].contacts_access is ContactsAccess.LIMITED


@pytest.mark.parametrize(
    ("code", "retryable"),
    [(403, False), (429, True), (500, True)],
)
async def test_two_gis_rejects_error_code_inside_successful_http_response(
    respx_mock: MockRouter,
    code: int,
    retryable: bool,
) -> None:
    respx_mock.get("https://catalog.api.2gis.com/2.0/region/search").respond(
        json={
            "meta": {"code": 200},
            "result": {"items": [{"id": "32", "name": "Москва", "type": "region"}]},
        }
    )
    respx_mock.get("https://catalog.api.2gis.com/3.0/items").respond(
        json={"meta": {"code": code}, "result": {}}
    )
    source = TwoGisSource(Settings(_env_file=None, two_gis_api_key=SecretStr("2gis-key")))

    with pytest.raises(SourceRequestError) as error:
        await source.search_page(criteria(), None)

    assert error.value.code == f"SOURCE_API_{code}"
    assert error.value.retryable is retryable


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"items": None},
        {"items": {}},
        {"items": [{}]},
        {"items": [{"id": "   ", "name": "Москва"}]},
        {"items": [{"id": True, "name": "Москва"}]},
        {"items": [{"id": "32"}]},
    ],
)
async def test_two_gis_rejects_malformed_region_payload(
    respx_mock: MockRouter,
    result: dict[str, Any],
) -> None:
    respx_mock.get("https://catalog.api.2gis.com/2.0/region/search").respond(
        json={"meta": {"code": 200}, "result": result}
    )
    respx_mock.get("https://catalog.api.2gis.com/3.0/items").respond(
        json={"meta": {"code": 200}, "result": {"items": [], "total": 0}}
    )
    source = TwoGisSource(Settings(_env_file=None, two_gis_api_key=SecretStr("2gis-key")))

    with pytest.raises(SourceRequestError) as error:
        await source.search_page(criteria(), None)

    assert error.value.code == "SOURCE_INVALID_PAYLOAD"


async def test_two_gis_reports_region_not_found_for_valid_empty_result(
    respx_mock: MockRouter,
) -> None:
    respx_mock.get("https://catalog.api.2gis.com/2.0/region/search").respond(
        json={"meta": {"code": 200}, "result": {"items": []}}
    )
    source = TwoGisSource(Settings(_env_file=None, two_gis_api_key=SecretStr("2gis-key")))

    with pytest.raises(SourceRequestError) as error:
        await source.search_page(criteria(), None)

    assert error.value.code == "SOURCE_REGION_NOT_FOUND"


async def test_yandex_maps_official_company_metadata(
    respx_mock: MockRouter,
    fixture_json: Callable[[str], dict[str, Any]],
) -> None:
    respx_mock.get("https://search-maps.yandex.ru/v1/").respond(
        json=fixture_json("yandex_page.json")
    )
    source = YandexSource(
        Settings(
            _env_file=None,
            yandex_maps_api_key=SecretStr("yandex-key"),
            yandex_storage_allowed=True,
        )
    )

    page = await source.search_page(criteria(), None)

    assert page.next_cursor == "50"
    assert page.items[0].source is SourceName.YANDEX
    assert page.items[0].rating == 4.9
    assert page.items[0].reviews_count == 137


async def test_retryable_failures_stop_after_success() -> None:
    attempts = 0
    sleeps: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    client = ResilientHttpClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_attempts=3,
        backoff_base_seconds=1,
        requests_per_second=100,
        sleeper=sleeps.append,
        jitter=lambda: 0,
    )

    payload = await client.request_json("GET", "https://source.test")

    assert payload == {"ok": True}
    assert attempts == 3
    assert sleeps == [1, 2]


async def test_retry_after_header_controls_delay() -> None:
    attempts = 0
    sleeps: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, request=request)
        return httpx.Response(200, json={}, request=request)

    client = ResilientHttpClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_attempts=2,
        backoff_base_seconds=1,
        requests_per_second=100,
        sleeper=sleeps.append,
        jitter=lambda: 0,
    )

    await client.request_json("GET", "https://source.test")

    assert sleeps == [7]


async def test_authentication_failure_is_not_retried() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, request=request)

    client = ResilientHttpClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_attempts=3,
        requests_per_second=100,
    )

    with pytest.raises(SourceRequestError) as error:
        await client.request_json("GET", "https://source.test")

    assert error.value.retryable is False
    assert attempts == 1
