import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

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
from app.sources.openstreetmap import OpenStreetMapSource
from app.sources.two_gis import TwoGisSource
from app.sources.yandex import YandexSource


def criteria() -> SearchCriteria:
    return SearchCriteria(city="Москва", query="детейлинг", max_results=300)


async def test_openstreetmap_builds_bounded_detailing_query_and_maps_contacts(
    respx_mock: MockRouter,
    fixture_json: Callable[[str], dict[str, Any]],
) -> None:
    route = respx_mock.post("https://overpass-api.de/api/interpreter").respond(
        json=fixture_json("overpass_page.json")
    )
    source = OpenStreetMapSource(Settings(_env_file=None, openstreetmap_enabled=True))

    page = await source.search_page(criteria(), None)

    request = route.calls[0].request
    body = request.content.decode()
    company = page.items[0]
    assert request.headers["User-Agent"] == "ClientParserMap/1.0"
    assert request.headers["Content-Type"].startswith("application/x-www-form-urlencoded")
    assert "amenity" in body and "car_wash" in body
    assert "shop" in body and "car_repair" in body
    assert "300" in body
    assert "~" not in parse_qs(body)["data"][0]
    assert page.next_cursor is None
    assert page.exhausted is True
    assert company.source is SourceName.OPENSTREETMAP
    assert company.source_id == "node/101"
    assert company.categories == (
        "amenity=car_wash",
        "shop=car_repair",
        "craft=painter",
        "office=company",
        "tourism=information",
        "healthcare=clinic",
    )
    assert company.address == "Москва, Тверская улица, 1"
    assert company.phones == (
        "+7 999 111-22-33",
        "+7 999 444-55-66",
        "+7 999 777-88-99",
    )
    assert company.emails == ("detail@example.com", "sales@example.com")
    assert company.websites == (
        "https://detail.example",
        "https://contact.detail.example",
        "https://profile.detail.example",
    )
    assert company.telegram == ("https://t.me/detail", "https://t.me/contact_detail")
    assert company.whatsapp == (
        "https://wa.me/79991112233",
        "https://wa.me/79994445566",
    )
    assert company.vk == ("https://vk.com/detail", "https://vk.com/contact_detail")
    assert company.instagram == (
        "https://instagram.com/detail",
        "https://instagram.com/contact_detail",
    )
    assert company.other_socials == (
        "https://facebook.com/detail",
        "https://facebook.com/contact_detail",
        "https://youtube.com/@detail",
        "https://youtube.com/@contact_detail",
        "https://x.com/detail",
        "https://x.com/contact_detail",
        "https://ok.ru/detail",
        "https://ok.ru/contact_detail",
    )
    assert company.working_hours == {"opening_hours": "Mo-Su 09:00-21:00"}
    assert page.items[1].source_id == "way/202"
    assert page.items[1].name == "Detail Way"
    assert page.items[1].address == "Арбат, 12"
    assert page.items[1].latitude == 55.75
    assert page.items[1].longitude == 37.61


async def test_openstreetmap_escapes_city_and_unknown_query_in_overpass_ql(
    respx_mock: MockRouter,
) -> None:
    city = 'Москва"];out;node["name"="injected'
    query = 'детейлинг.*[x](?=y)";out;'
    route = respx_mock.post("https://overpass-api.de/api/interpreter").respond(
        json={"elements": []}
    )
    source = OpenStreetMapSource(Settings(_env_file=None, openstreetmap_enabled=True))

    await source.search_page(SearchCriteria(city=city, query=query, max_results=1), None)

    overpass_ql = parse_qs(route.calls[0].request.content.decode())["data"][0]
    assert f'["name"={json.dumps(city, ensure_ascii=False)}]' in overpass_ql
    assert f'["name"~{json.dumps(re.escape(query), ensure_ascii=False)},i]' in overpass_ql
    assert overpass_ql.count("nwr[") == 5
    assert overpass_ql.count("out;") == 6
    assert "]->.searchArea;" in overpass_ql


async def test_openstreetmap_rejects_non_list_elements(respx_mock: MockRouter) -> None:
    respx_mock.post("https://overpass-api.de/api/interpreter").respond(json={"elements": {}})
    source = OpenStreetMapSource(Settings(_env_file=None, openstreetmap_enabled=True))

    with pytest.raises(SourceRequestError) as error:
        await source.search_page(criteria(), None)

    assert error.value.code == "SOURCE_INVALID_PAYLOAD"


async def test_openstreetmap_skips_element_with_invalid_coordinate_pair(
    respx_mock: MockRouter,
    fixture_json: Callable[[str], dict[str, Any]],
) -> None:
    payload = fixture_json("overpass_page.json")
    payload["elements"].append(
        {
            "type": "node",
            "id": 303,
            "lat": 91,
            "lon": 37.62,
            "tags": {"name": "Некорректная координата"},
        }
    )
    respx_mock.post("https://overpass-api.de/api/interpreter").respond(json=payload)
    source = OpenStreetMapSource(Settings(_env_file=None, openstreetmap_enabled=True))

    page = await source.search_page(criteria(), None)

    assert [company.source_id for company in page.items] == ["node/101", "way/202"]


async def test_openstreetmap_raises_retryable_error_for_overpass_remark(
    respx_mock: MockRouter,
) -> None:
    respx_mock.post("https://overpass-api.de/api/interpreter").respond(
        json={"remark": "runtime error: Query timed out", "elements": []}
    )
    source = OpenStreetMapSource(Settings(_env_file=None, openstreetmap_enabled=True))

    with pytest.raises(SourceRequestError) as error:
        await source.search_page(criteria(), None)

    assert error.value.code == "SOURCE_OVERPASS_REMARK"
    assert error.value.retryable is True


async def test_openstreetmap_cursor_does_not_repeat_request(respx_mock: MockRouter) -> None:
    route = respx_mock.post("https://overpass-api.de/api/interpreter").respond(
        json={"elements": []}
    )
    source = OpenStreetMapSource(Settings(_env_file=None, openstreetmap_enabled=True))

    page = await source.search_page(criteria(), "already-read")

    assert page.items == ()
    assert page.exhausted is True
    assert route.call_count == 0


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
