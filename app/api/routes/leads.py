from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.dependencies import SessionDependency, require_api_key
from app.core.enums import CMS, LeadState, SourceName, WebsiteStatus
from app.db.models import Company, CompanySource
from app.schemas.api import LeadPage, LeadSummary

router = APIRouter(prefix="/leads", tags=["leads"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=LeadPage)
async def list_leads(
    session: SessionDependency,
    city: str | None = None,
    query: str | None = None,
    min_score: int = Query(default=0, ge=0, le=100),
    cms: CMS | None = None,
    website_status: WebsiteStatus | None = None,
    source: SourceName | None = None,
    contacts_found: bool | None = None,
    lead_state: LeadState | None = LeadState.QUALIFIED,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> LeadPage:
    filters = [Company.site_opportunity_score >= min_score]
    if city:
        filters.append(Company.city == city)
    if query:
        filters.append(
            Company.name.ilike(f"%{query}%") | Company.primary_category.ilike(f"%{query}%")
        )
    if cms is not None:
        filters.append(Company.cms == cms)
    if website_status is not None:
        filters.append(Company.website_status == website_status)
    if contacts_found is not None:
        filters.append(Company.contacts_found.is_(contacts_found))
    if lead_state is not None:
        filters.append(Company.lead_state == lead_state)
    if source is not None:
        filters.append(
            Company.id.in_(select(CompanySource.company_id).where(CompanySource.source == source))
        )
    total = int(
        await session.scalar(select(func.count()).select_from(Company).where(*filters)) or 0
    )
    companies = list(
        (
            await session.scalars(
                select(Company)
                .where(*filters)
                .options(selectinload(Company.sources))
                .order_by(Company.site_opportunity_score.desc(), Company.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
    return LeadPage(
        items=tuple(_lead_summary(company) for company in companies),
        total=total,
        limit=limit,
        offset=offset,
    )


def _lead_summary(company: Company) -> LeadSummary:
    return LeadSummary(
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
        preferred_contact_type=company.preferred_contact_type,
        preferred_contact_value=company.preferred_contact_value,
        lead_state=company.lead_state,
        sources=tuple(source.source for source in company.sources),
    )
