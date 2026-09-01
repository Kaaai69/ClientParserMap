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


SESSION_COOKIE = "cp_session"


def key_is_valid(settings: Settings, supplied: str | None) -> bool:
    configured = settings.api_auth_key
    if configured is None:
        return True
    return secrets.compare_digest(configured.get_secret_value(), supplied or "")


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Accept the key from the header or from the browser session cookie.

    Scripts keep using X-API-Key; the console signs in once and then rides on
    the cookie, so the key is never held in JavaScript-readable storage.
    """
    settings = settings_from_request(request)
    if settings.api_auth_key is None:
        return
    supplied = x_api_key or request.cookies.get(SESSION_COOKIE)
    if not key_is_valid(settings, supplied):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Неверный API-ключ"},
        )
