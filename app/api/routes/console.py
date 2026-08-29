from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["console"])

CONSOLE_FILE = Path(__file__).resolve().parents[2] / "web" / "index.html"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def console() -> HTMLResponse:
    """Serve the operator console.

    The page itself is public: every call it makes carries the API key, so the
    key never has to be baked into the markup.
    """
    return HTMLResponse(CONSOLE_FILE.read_text(encoding="utf-8"))
