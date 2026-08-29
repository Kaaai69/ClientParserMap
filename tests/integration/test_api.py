from collections.abc import AsyncIterator

import httpx
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.base import Base
from app.db.session import Database
from app.main import create_app


class HealthyQueue:
    async def enqueue_search(
        self,
        job_id: int,
        deterministic_id: str,
        job_timeout: int,
    ) -> None:
        return None

    async def ping(self) -> bool:
        return True


async def client_for(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    database = Database("sqlite+aiosqlite:///:memory:")
    await create_schema(database.engine)
    app = create_app(
        settings=settings,
        database=database,
        queue=HealthyQueue(),
        start_dispatcher=False,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await database.dispose()


async def create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def test_post_search_creates_pending_job() -> None:
    settings = Settings(
        _env_file=None,
        google_places_api_key=SecretStr("google-key"),
    )
    async for client in client_for(settings):
        response = await client.post(
            "/search",
            json={"city": "Москва", "query": "детейлинг", "max_results": 300},
        )

        assert response.status_code == 202
        payload = response.json()
        assert payload["status"] == "PENDING"
        detail = await client.get(f"/search/{payload['id']}")
        assert detail.status_code == 200
        assert detail.json()["query"] == "детейлинг"


async def test_search_rejected_when_no_source_enabled() -> None:
    async for client in client_for(Settings(_env_file=None)):
        response = await client.post(
            "/search",
            json={"city": "Москва", "query": "детейлинг"},
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "NO_ENABLED_SOURCES"


async def test_disabled_requested_source_is_rejected() -> None:
    settings = Settings(
        _env_file=None,
        google_places_api_key=SecretStr("google-key"),
    )
    async for client in client_for(settings):
        response = await client.post(
            "/search",
            json={
                "city": "Москва",
                "query": "детейлинг",
                "sources": ["2gis"],
            },
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "SOURCE_NOT_ENABLED"


async def test_api_key_is_required_but_health_is_public() -> None:
    settings = Settings(
        _env_file=None,
        google_places_api_key=SecretStr("google-key"),
        api_auth_key=SecretStr("secret-api-key"),
    )
    async for client in client_for(settings):
        denied = await client.get("/search/1")
        accepted = await client.get("/search/1", headers={"X-API-Key": "secret-api-key"})
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")

        assert denied.status_code == 401
        assert accepted.status_code == 404
        assert live.status_code == 200
        assert ready.status_code == 200


async def test_meta_reports_enabled_sources_and_sheet_url() -> None:
    settings = Settings(
        _env_file=None,
        openstreetmap_enabled=True,
        google_sheets_spreadsheet_id="sheet-123",
        google_service_account_file="/run/secrets/google/service-account.json",
    )
    async for client in client_for(settings):
        response = await client.get("/meta")

        assert response.status_code == 200
        payload = response.json()
        assert payload["enabled_sources"] == ["openstreetmap"]
        assert payload["sheets_enabled"] is True
        assert payload["spreadsheet_url"] == "https://docs.google.com/spreadsheets/d/sheet-123/edit"
        assert payload["auth_required"] is False


async def test_meta_requires_the_api_key_when_one_is_configured() -> None:
    settings = Settings(
        _env_file=None,
        openstreetmap_enabled=True,
        api_auth_key=SecretStr("secret"),
    )
    async for client in client_for(settings):
        assert (await client.get("/meta")).status_code == 401

        response = await client.get("/meta", headers={"X-API-Key": "secret"})

        assert response.status_code == 200
        assert response.json()["auth_required"] is True


async def test_list_searches_returns_newest_first() -> None:
    settings = Settings(_env_file=None, openstreetmap_enabled=True)
    async for client in client_for(settings):
        for query in ("детейлинг", "шиномонтаж"):
            created = await client.post("/search", json={"city": "Москва", "query": query})
            assert created.status_code == 202

        response = await client.get("/search?limit=10")

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 2
        assert [item["query"] for item in payload["items"]] == ["шиномонтаж", "детейлинг"]
        assert payload["items"][0]["requested_sources"] == ["openstreetmap"]


async def test_console_page_is_served_without_the_api_key() -> None:
    settings = Settings(_env_file=None, api_auth_key=SecretStr("secret"))
    async for client in client_for(settings):
        response = await client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Парсер" in response.text


async def test_batch_search_queues_one_job_per_niche() -> None:
    settings = Settings(_env_file=None, openstreetmap_enabled=True)
    async for client in client_for(settings):
        response = await client.post(
            "/search/batch",
            json={"city": "Москва", "preset": "auto", "max_results": 100},
        )

        assert response.status_code == 202
        payload = response.json()
        assert payload["city"] == "Москва"
        assert payload["preset"] == "auto"
        queries = [item["query"] for item in payload["jobs"]]
        assert "детейлинг" in queries and "шиномонтаж" in queries
        assert len(set(item["id"] for item in payload["jobs"])) == len(queries)
        assert all(item["status"] == "PENDING" for item in payload["jobs"])

        listed = await client.get(f"/search?limit={len(queries)}")
        assert listed.json()["total"] == len(queries)


async def test_batch_search_accepts_explicit_queries() -> None:
    settings = Settings(_env_file=None, openstreetmap_enabled=True)
    async for client in client_for(settings):
        response = await client.post(
            "/search/batch",
            json={"city": "Казань", "queries": ["барбершоп", "кафе"]},
        )

        assert response.status_code == 202
        assert [item["query"] for item in response.json()["jobs"]] == ["барбершоп", "кафе"]


async def test_batch_search_rejects_an_unknown_preset() -> None:
    settings = Settings(_env_file=None, openstreetmap_enabled=True)
    async for client in client_for(settings):
        response = await client.post("/search/batch", json={"city": "Москва", "preset": "нет"})

        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "PRESET_NOT_FOUND"


async def test_batch_search_requires_exactly_one_query_source() -> None:
    settings = Settings(_env_file=None, openstreetmap_enabled=True)
    async for client in client_for(settings):
        assert (await client.post("/search/batch", json={"city": "Москва"})).status_code == 422

        both = await client.post(
            "/search/batch",
            json={"city": "Москва", "preset": "auto", "queries": ["кафе"]},
        )

        assert both.status_code == 422


async def test_batch_search_refuses_a_source_that_is_not_enabled() -> None:
    settings = Settings(_env_file=None, openstreetmap_enabled=True)
    async for client in client_for(settings):
        response = await client.post(
            "/search/batch",
            json={"city": "Москва", "preset": "auto", "sources": ["google"]},
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "SOURCE_NOT_ENABLED"


async def test_batch_search_caps_the_number_of_niches() -> None:
    settings = Settings(_env_file=None, openstreetmap_enabled=True, max_batch_searches=2)
    async for client in client_for(settings):
        response = await client.post(
            "/search/batch",
            json={"city": "Москва", "queries": ["кафе", "бар", "пекарня"]},
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "BATCH_TOO_LARGE"


async def test_meta_lists_the_niche_presets() -> None:
    settings = Settings(_env_file=None, openstreetmap_enabled=True)
    async for client in client_for(settings):
        payload = (await client.get("/meta")).json()

        presets = {item["id"]: item for item in payload["niche_presets"]}
        assert "small_business" in presets
        assert presets["small_business"]["title"] == "Малый бизнес"
        assert len(presets["small_business"]["queries"]) >= 10
        assert payload["max_batch_searches"] == 50
