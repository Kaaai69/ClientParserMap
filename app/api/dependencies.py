import secrets
from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import Database


def settings_from_request(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def database_from_request(request: Request) -> Database:
    return cast(Database, request.app.state.database)


async def session_from_request(request: Request) -> AsyncIterator[AsyncSession]:
    database = database_from_request(request)
    async with database.session_factory() as session:
        yield session


SessionDependency = Annotated[AsyncSession, Depends(session_from_request)]
SettingsDependency = Annotated[Settings, Depends(settings_from_request)]


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    configured = settings_from_request(request).api_auth_key
    if configured is None:
        return
    supplied = x_api_key or ""
    if not secrets.compare_digest(configured.get_secret_value(), supplied):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Неверный API-ключ"},
        )
