from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class SearchJobSummary(ApiModel):
    id: int
    city: str
    query: str
    requested_sources: tuple[SourceName, ...]
    status: JobStatus
    stage: JobStage
    created_at: datetime
    finished_at: datetime | None
    found_count: int
    unique_count: int
    lead_count: int
    exported_count: int
    error_count: int


class SearchJobPage(ApiModel):
    items: tuple[SearchJobSummary, ...]
    total: int
    limit: int
    offset: int


class SessionRequest(ApiModel):
    key: str = Field(min_length=1, max_length=500)


class NichePresetResponse(ApiModel):
    id: str
    title: str
    queries: tuple[str, ...]


class MetaResponse(ApiModel):
    enabled_sources: tuple[SourceName, ...]
    sheets_enabled: bool
    spreadsheet_url: str | None
    lead_score_threshold: int
    auth_required: bool
    niche_presets: tuple[NichePresetResponse, ...]
    max_batch_searches: int


class BatchSearchRequest(ApiModel):
    city: str = Field(min_length=1, max_length=200)
    preset: str | None = None
    queries: tuple[str, ...] | None = None
    sources: tuple[SourceName, ...] | None = None
    min_rating: float | None = Field(default=None, ge=0, le=5)
    min_reviews: int | None = Field(default=None, ge=0)
    max_results: int = Field(default=500, ge=1, le=5000)

    @field_validator("city")
    @classmethod
    def strip_non_empty_city(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("queries")
    @classmethod
    def queries_are_clean_and_unique(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        cleaned = tuple(" ".join(item.split()) for item in value)
        if not cleaned or any(not item for item in cleaned):
            raise ValueError("queries must not be blank")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("queries must be unique")
        return cleaned

    @model_validator(mode="after")
    def exactly_one_source_of_queries(self) -> "BatchSearchRequest":
        if (self.preset is None) == (self.queries is None):
            raise ValueError("provide either preset or queries")
        return self


class BatchSearchItem(ApiModel):
    id: int
    query: str
    status: JobStatus


class BatchSearchAccepted(ApiModel):
    city: str
    preset: str | None
    jobs: tuple[BatchSearchItem, ...]


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
