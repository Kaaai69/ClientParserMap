from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import (
    SessionDependency,
    SettingsDependency,
    require_api_key,
)
from app.core.enums import SourceName
from app.db.models import SearchJob
from app.db.repositories import SearchJobRepository
from app.schemas.api import SearchAccepted, SearchJobResponse, SearchRequest
from app.schemas.domain import SearchCriteria

router = APIRouter(prefix="/search", tags=["search"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=SearchAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_search(
    request: SearchRequest,
    session: SessionDependency,
    settings: SettingsDependency,
) -> SearchAccepted:
    enabled = set(settings.enabled_sources)
    requested = request.sources or settings.enabled_sources
    if not enabled or not requested:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "NO_ENABLED_SOURCES",
                "message": "Не настроен ни один источник поиска",
            },
        )
    disabled = set(requested) - enabled
    if disabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "SOURCE_NOT_ENABLED",
                "message": "Запрошенный источник не настроен или не разрешён лицензией",
                "sources": sorted(source.value for source in disabled),
            },
        )
    criteria = SearchCriteria(
        city=request.city,
        query=request.query,
        min_rating=request.min_rating,
        min_reviews=request.min_reviews,
        max_results=request.max_results,
    )
    job = await SearchJobRepository(session).create_with_outbox(criteria, tuple(requested))
    await session.commit()
    return SearchAccepted(id=job.id, status=job.status)


@router.get("/{job_id}", response_model=SearchJobResponse)
async def get_search(
    job_id: int,
    session: SessionDependency,
) -> SearchJobResponse:
    job = await SearchJobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SEARCH_NOT_FOUND", "message": "Запуск поиска не найден"},
        )
    return _job_response(job)


def _job_response(job: SearchJob) -> SearchJobResponse:
    return SearchJobResponse(
        id=job.id,
        city=job.city,
        query=job.query,
        min_rating=job.min_rating,
        min_reviews=job.min_reviews,
        max_results=job.max_results,
        requested_sources=tuple(SourceName(value) for value in job.requested_sources),
        status=job.status,
        stage=job.stage,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        found_count=job.found_count,
        filtered_out_count=job.filtered_out_count,
        unique_count=job.unique_count,
        analyzed_count=job.analyzed_count,
        lead_count=job.lead_count,
        contactable_lead_count=job.contactable_lead_count,
        exported_count=job.exported_count,
        error_count=job.error_count,
        errors=tuple(job.errors),
    )
