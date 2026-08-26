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
