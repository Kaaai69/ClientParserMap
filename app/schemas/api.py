from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.enums import (
    CMS,
    ContactType,
    JobStage,
    JobStatus,
    LeadState,
    SourceName,
    WebsiteStatus,
    WebsiteType,
)
from app.schemas.domain import SearchCriteria


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchRequest(SearchCriteria):
    sources: tuple[SourceName, ...] | None = None

    @field_validator("sources")
    @classmethod
    def sources_are_unique(
        cls,
        value: tuple[SourceName, ...] | None,
    ) -> tuple[SourceName, ...] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("sources must be unique")
        return value


class SearchAccepted(ApiModel):
    id: int
    status: JobStatus


class SearchJobResponse(ApiModel):
    id: int
    city: str
    query: str
    min_rating: float | None
    min_reviews: int | None
    max_results: int
    requested_sources: tuple[SourceName, ...]
    status: JobStatus
    stage: JobStage
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    found_count: int
    filtered_out_count: int
    unique_count: int
    analyzed_count: int
    lead_count: int
    contactable_lead_count: int
    exported_count: int
    error_count: int
    errors: tuple[dict[str, Any], ...]


class LeadSummary(ApiModel):
    id: int
    name: str
    category: str | None
    city: str
    address: str | None
    website: str | None
    rating: float | None
    reviews_count: int | None
    website_status: WebsiteStatus
    cms: CMS
    website_type: WebsiteType
    site_opportunity_score: int
    contactability_score: int
    preferred_contact_type: ContactType | None
    preferred_contact_value: str | None
    lead_state: LeadState
    sources: tuple[SourceName, ...]


class LeadPage(ApiModel):
    items: tuple[LeadSummary, ...]
    total: int
    limit: int
    offset: int


class CompanySourceResponse(ApiModel):
    source: SourceName
    source_id: str
    name: str
    categories: tuple[str, ...]
    address: str | None
    rating: float | None
    reviews_count: int | None


class CompanyContactResponse(ApiModel):
    type: ContactType
    value: str
    normalized_value: str
    is_primary: bool
    sources: tuple[str, ...]


class WebsiteCheckResponse(ApiModel):
    status: WebsiteStatus
    final_url: str | None
    http_status: int | None
    https_enabled: bool
    cms: CMS
    cms_confidence: float
    website_type: WebsiteType
    error_code: str | None
    checked_at: datetime


class CompanyDetailResponse(ApiModel):
    id: int
    name: str
    category: str | None
    city: str
    address: str | None
    website: str | None
    rating: float | None
    reviews_count: int | None
    website_status: WebsiteStatus
    cms: CMS
    website_type: WebsiteType
    site_opportunity_score: int
    contactability_score: int
    reasons: tuple[str, ...]
    lead_state: LeadState
    sources: tuple[CompanySourceResponse, ...]
    contacts: tuple[CompanyContactResponse, ...]
    latest_website_check: WebsiteCheckResponse | None


class HealthResponse(ApiModel):
    status: str
