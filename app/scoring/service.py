import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.enums import (
    CMS,
    ContactType,
    LeadState,
    WebsiteStatus,
    WebsiteType,
)
from app.core.errors import ConfigurationError
from app.schemas.domain import ContactValue


class RuleModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WebsiteRules(RuleModel):
    no_website: int = Field(ge=0, le=100)
    dead_or_timeout: int = Field(ge=0, le=100)
    parked: int = Field(ge=0, le=100)
    placeholder: int = Field(ge=0, le=100)
    business_card: int = Field(ge=0, le=100)
    tilda_wix_nethouse: int = Field(ge=0, le=100)
    flexbe_creatium_lpmotor: int = Field(ge=0, le=100)


class BusinessRules(RuleModel):
    rating_threshold: float = Field(ge=0, le=5)
    rating_weight: int = Field(ge=0, le=100)
    review_thresholds: tuple[int, ...]
    review_weight: int = Field(ge=0, le=100)


class ContactRules(RuleModel):
    phone: int = Field(ge=0, le=100)
    whatsapp_or_telegram: int = Field(ge=0, le=100)
    email: int = Field(ge=0, le=100)
    social_or_other: int = Field(ge=0, le=100)


class ScoringRules(RuleModel):
    website: WebsiteRules
    business: BusinessRules
    contact: ContactRules

    @classmethod
    def load(cls, path: Path) -> "ScoringRules":
        try:
            with path.open("rb") as rules_file:
                payload = tomllib.load(rules_file)
            rules = cls.model_validate(payload)
        except (OSError, tomllib.TOMLDecodeError, ValidationError) as error:
            raise ConfigurationError("Некорректный файл правил оценки лидов") from error
        if (
            not rules.business.review_thresholds
            or any(value < 0 for value in rules.business.review_thresholds)
            or tuple(sorted(set(rules.business.review_thresholds)))
            != rules.business.review_thresholds
        ):
            raise ConfigurationError(
                "Пороги количества отзывов должны быть уникальными и возрастающими"
            )
        return rules


class ScoringInput(RuleModel):
    website_status: WebsiteStatus
    website_type: WebsiteType = WebsiteType.NORMAL
    cms: CMS = CMS.CUSTOM_OR_UNKNOWN
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews_count: int | None = Field(default=None, ge=0)
    contacts: tuple[ContactValue, ...] = ()


class ScoringResult(RuleModel):
    site_opportunity_score: int = Field(ge=0, le=100)
    contactability_score: int = Field(ge=0, le=100)
    reasons: tuple[str, ...]
    preferred_contact_type: ContactType | None = None
    preferred_contact_value: str | None = None
    lead_state: LeadState


def score_company(
    company: ScoringInput,
    rules: ScoringRules,
    threshold: int,
) -> ScoringResult:
    if not 0 <= threshold <= 100:
        raise ValueError("threshold must be between 0 and 100")

    website_candidates = _website_candidates(company, rules.website)
    reasons: list[str] = []
    if website_candidates:
        website_score, website_reason = max(website_candidates, key=lambda item: item[0])
        reasons.append(website_reason)
    else:
        website_score = 0

    if company.rating is not None and company.rating >= rules.business.rating_threshold:
        website_score += rules.business.rating_weight
        reasons.append(f"Рейтинг не ниже {rules.business.rating_threshold:g}")
    if company.reviews_count is not None:
        for review_threshold in rules.business.review_thresholds:
            if company.reviews_count >= review_threshold:
                website_score += rules.business.review_weight
                reasons.append(f"Не менее {review_threshold} отзывов")
    website_score = min(100, max(0, website_score))

    preferred = _preferred_contact(company.contacts)
    contactability_score = (
        _contact_score(preferred.type, rules.contact) if preferred is not None else 0
    )
    if website_score < threshold:
        lead_state = LeadState.BELOW_THRESHOLD
    elif preferred is None:
        lead_state = LeadState.NO_CONTACTS
    else:
        lead_state = LeadState.QUALIFIED
    return ScoringResult(
        site_opportunity_score=website_score,
        contactability_score=contactability_score,
        reasons=tuple(reasons),
        preferred_contact_type=preferred.type if preferred else None,
        preferred_contact_value=preferred.value if preferred else None,
        lead_state=lead_state,
    )


def _website_candidates(
    company: ScoringInput,
    rules: WebsiteRules,
) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []
    if company.website_status is WebsiteStatus.NO_WEBSITE:
        candidates.append((rules.no_website, "Нет собственного сайта"))
    if company.website_status in {WebsiteStatus.DEAD, WebsiteStatus.TIMEOUT}:
        candidates.append((rules.dead_or_timeout, "Сайт недоступен"))
    if company.website_status is WebsiteStatus.PARKED:
        candidates.append((rules.parked, "Сайт припаркован"))
    if company.website_status is WebsiteStatus.PLACEHOLDER:
        candidates.append((rules.placeholder, "Сайт-заглушка"))
    if company.website_type is WebsiteType.BUSINESS_CARD:
        candidates.append((rules.business_card, "Сайт-визитка"))
    if company.cms in {CMS.TILDA, CMS.WIX, CMS.NETHOUSE}:
        candidates.append((rules.tilda_wix_nethouse, f"Сайт создан на {company.cms.value}"))
    if company.cms in {CMS.FLEXBE, CMS.CREATIUM, CMS.LPMOTOR}:
        candidates.append((rules.flexbe_creatium_lpmotor, f"Сайт создан на {company.cms.value}"))
    return candidates


def _preferred_contact(contacts: tuple[ContactValue, ...]) -> ContactValue | None:
    priority = (
        ContactType.PHONE,
        ContactType.WHATSAPP,
        ContactType.TELEGRAM,
        ContactType.EMAIL,
        ContactType.VK,
        ContactType.INSTAGRAM,
        ContactType.OTHER,
    )
    for contact_type in priority:
        match = next((item for item in contacts if item.type is contact_type), None)
        if match is not None:
            return match
    return None


def _contact_score(contact_type: ContactType, rules: ContactRules) -> int:
    if contact_type is ContactType.PHONE:
        return rules.phone
    if contact_type in {ContactType.WHATSAPP, ContactType.TELEGRAM}:
        return rules.whatsapp_or_telegram
    if contact_type is ContactType.EMAIL:
        return rules.email
    return rules.social_or_other
