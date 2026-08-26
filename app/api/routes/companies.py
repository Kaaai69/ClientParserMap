from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import SessionDependency, require_api_key
from app.db.repositories import CompanyRepository
from app.schemas.api import (
    CompanyContactResponse,
    CompanyDetailResponse,
    CompanySourceResponse,
    WebsiteCheckResponse,
)

router = APIRouter(
    prefix="/companies",
    tags=["companies"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/{company_id}", response_model=CompanyDetailResponse)
async def get_company(
    company_id: int,
    session: SessionDependency,
) -> CompanyDetailResponse:
    company = await CompanyRepository(session).get_detail(company_id)
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "COMPANY_NOT_FOUND", "message": "Компания не найдена"},
        )
    latest = max(company.website_checks, key=lambda item: item.checked_at, default=None)
    latest_response = (
        WebsiteCheckResponse(
            status=latest.status,
            final_url=latest.final_url,
            http_status=latest.http_status,
            https_enabled=latest.https_enabled,
            cms=latest.cms,
            cms_confidence=latest.cms_confidence,
            website_type=latest.website_type,
            error_code=latest.error_code,
            checked_at=latest.checked_at,
        )
        if latest is not None
        else None
    )
    return CompanyDetailResponse(
        id=company.id,
        name=company.name,
        category=company.primary_category,
        city=company.city,
        address=company.address,
        website=company.canonical_website,
        rating=company.rating,
        reviews_count=company.reviews_count,
        website_status=company.website_status,
        cms=company.cms,
        website_type=company.website_type,
        site_opportunity_score=company.site_opportunity_score,
        contactability_score=company.contactability_score,
        reasons=tuple(company.lead_reasons),
        lead_state=company.lead_state,
        sources=tuple(
            CompanySourceResponse(
                source=item.source,
                source_id=item.source_id,
                name=item.name,
                categories=tuple(item.categories),
                address=item.address,
                rating=item.rating,
                reviews_count=item.reviews_count,
            )
            for item in company.sources
        ),
        contacts=tuple(
            CompanyContactResponse(
                type=item.type,
                value=item.value,
                normalized_value=item.normalized_value,
                is_primary=item.is_primary,
                sources=tuple(evidence.source for evidence in item.sources),
            )
            for item in company.contacts
        ),
        latest_website_check=latest_response,
    )
