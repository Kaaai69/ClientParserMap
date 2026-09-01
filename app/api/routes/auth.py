from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.dependencies import SESSION_COOKIE, SettingsDependency, key_is_valid
from app.schemas.api import SessionRequest

router = APIRouter(prefix="/auth", tags=["auth"])

# Long enough that an operator signs in about twice a year.
SESSION_MAX_AGE_SECONDS = 180 * 24 * 60 * 60


def _over_https(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return request.url.scheme == "https" or forwarded == "https"


@router.post("/session", status_code=status.HTTP_204_NO_CONTENT)
async def create_session(
    request: Request,
    payload: SessionRequest,
    settings: SettingsDependency,
    response: Response,
) -> Response:
    """Exchange the API key for a session cookie the browser keeps on its own."""
    if not key_is_valid(settings, payload.key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Неверный API-ключ"},
        )
    response.status_code = status.HTTP_204_NO_CONTENT
    response.set_cookie(
        SESSION_COOKIE,
        payload.key,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=_over_https(request),
        # Strict: nothing else on the internet should be able to drive a search.
        samesite="strict",
        path="/",
    )
    return response


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(response: Response) -> Response:
    response.status_code = status.HTTP_204_NO_CONTENT
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
