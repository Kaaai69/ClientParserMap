from fastapi import APIRouter, Depends

from app.api.dependencies import SettingsDependency, require_api_key
from app.presets import load_presets
from app.schemas.api import MetaResponse, NichePresetResponse

router = APIRouter(prefix="/meta", tags=["meta"], dependencies=[Depends(require_api_key)])

SPREADSHEET_URL_TEMPLATE = "https://docs.google.com/spreadsheets/d/{id}/edit"


@router.get("", response_model=MetaResponse)
async def read_meta(settings: SettingsDependency) -> MetaResponse:
    """Expose the runtime configuration the operator console renders itself from."""
    spreadsheet_id = settings.google_sheets_spreadsheet_id
    return MetaResponse(
        enabled_sources=settings.enabled_sources,
        sheets_enabled=settings.sheets_enabled,
        spreadsheet_url=(
            SPREADSHEET_URL_TEMPLATE.format(id=spreadsheet_id)
            if settings.sheets_enabled and spreadsheet_id
            else None
        ),
        lead_score_threshold=settings.lead_score_threshold,
        auth_required=settings.api_auth_key is not None,
        niche_presets=tuple(
            NichePresetResponse(id=item.id, title=item.title, queries=item.queries)
            for item in load_presets(settings.niche_presets_file).preset
        ),
        max_batch_searches=settings.max_batch_searches,
    )
