import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base


@pytest.fixture
def fixture_json() -> Callable[[str], dict[str, Any]]:
    def load(name: str) -> dict[str, Any]:
        path = Path(__file__).parent / "fixtures" / name
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))

    return load


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as value:
        yield value
        await value.rollback()
    await engine.dispose()
