from typing import Protocol

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text

from app.schemas.api import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


class HealthQueue(Protocol):
    async def ping(self) -> bool: ...


@router.get("/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse:
    try:
        async with request.app.state.database.session_factory() as session:
            await session.execute(text("SELECT 1"))
        queue: HealthQueue = request.app.state.queue
        if not await queue.ping():
            raise RuntimeError("redis ping failed")
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "NOT_READY", "message": "Зависимости сервиса недоступны"},
        ) from error
    return HealthResponse(status="ok")
