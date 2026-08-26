from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import ContactsAccess, ContactType, SourceName


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SearchCriteria(FrozenModel):
    city: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=300)
    min_rating: float | None = Field(default=None, ge=0, le=5)
    min_reviews: int | None = Field(default=None, ge=0)
    max_results: int = Field(default=500, ge=1, le=5000)

    @field_validator("city", "query")
    @classmethod
    def strip_non_empty_text(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("must not be blank")
        return value


class ContactValue(FrozenModel):
    type: ContactType
    value: str = Field(min_length=1, max_length=1000)
    normalized_value: str = Field(min_length=1, max_length=1000)
    is_primary: bool = False


class SourceCompany(FrozenModel):
    source: SourceName
    source_id: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=500)
    city: str = Field(min_length=1, max_length=200)
    categories: tuple[str, ...] = ()
    address: str | None = None
    primary_phone: str | None = None
    phones: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    websites: tuple[str, ...] = ()
    telegram: tuple[str, ...] = ()
    whatsapp: tuple[str, ...] = ()
    vk: tuple[str, ...] = ()
    instagram: tuple[str, ...] = ()
    other_socials: tuple[str, ...] = ()
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews_count: int | None = Field(default=None, ge=0)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    working_hours: dict[str, Any] | None = None
    contacts_access: ContactsAccess = ContactsAccess.FULL

    @field_validator("name", "city")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def coordinates_are_a_pair(self) -> "SourceCompany":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class NormalizedCompany(FrozenModel):
    name: str
    address: str | None = None
    phone_numbers: tuple[str, ...] = ()
    websites: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    contacts: tuple[ContactValue, ...] = ()


class SourcePage(FrozenModel):
    items: tuple[SourceCompany, ...]
    next_cursor: str | None = None
    exhausted: bool = False
