from pathlib import Path

import pytest

from app.core.enums import (
    CMS,
    ContactType,
    LeadState,
    WebsiteStatus,
    WebsiteType,
)
from app.core.errors import ConfigurationError
from app.schemas.domain import ContactValue
from app.scoring.service import ScoringInput, ScoringRules, score_company

RULES_PATH = Path("app/scoring/scoring_rules.toml")


@pytest.fixture
def rules() -> ScoringRules:
    return ScoringRules.load(RULES_PATH)


def contact(contact_type: ContactType, value: str) -> ContactValue:
    return ContactValue(type=contact_type, value=value, normalized_value=value)


def score_input(
    *,
    status: WebsiteStatus = WebsiteStatus.ONLINE,
    website_type: WebsiteType = WebsiteType.NORMAL,
    cms: CMS = CMS.CUSTOM_OR_UNKNOWN,
    rating: float | None = None,
    reviews: int | None = None,
    contacts: tuple[ContactValue, ...] = (),
) -> ScoringInput:
    return ScoringInput(
        website_status=status,
        website_type=website_type,
        cms=cms,
        rating=rating,
        reviews_count=reviews,
        contacts=contacts,
    )


def test_no_website_is_100_and_reason_is_russian(rules: ScoringRules) -> None:
    result = score_company(
        score_input(
            status=WebsiteStatus.NO_WEBSITE,
            contacts=(contact(ContactType.PHONE, "+79991234567"),),
        ),
        rules,
        50,
    )

    assert result.site_opportunity_score == 100
    assert result.contactability_score == 100
    assert result.lead_state is LeadState.QUALIFIED
    assert "Нет собственного сайта" in result.reasons


def test_tilda_business_signals_stack_but_clamp(rules: ScoringRules) -> None:
    result = score_company(
        score_input(
            cms=CMS.TILDA,
            rating=4.9,
            reviews=137,
            contacts=(contact(ContactType.EMAIL, "sales@example.ru"),),
        ),
        rules,
        50,
    )

    assert result.site_opportunity_score == 60
    assert result.contactability_score == 70
    assert result.preferred_contact_value == "sales@example.ru"


def test_high_opportunity_without_contact_is_no_contacts(rules: ScoringRules) -> None:
    result = score_company(
        score_input(status=WebsiteStatus.DEAD),
        rules,
        50,
    )

    assert result.lead_state is LeadState.NO_CONTACTS


def test_below_threshold_wins_even_without_contacts(rules: ScoringRules) -> None:
    result = score_company(score_input(), rules, 50)

    assert result.site_opportunity_score == 0
    assert result.lead_state is LeadState.BELOW_THRESHOLD


def test_strongest_website_signal_is_counted_once(rules: ScoringRules) -> None:
    result = score_company(
        score_input(
            status=WebsiteStatus.PLACEHOLDER,
            website_type=WebsiteType.BUSINESS_CARD,
            cms=CMS.TILDA,
            contacts=(contact(ContactType.TELEGRAM, "nyra"),),
        ),
        rules,
        50,
    )

    assert result.site_opportunity_score == 90
    assert "Сайт-заглушка" in result.reasons
    assert "Сайт-визитка" not in result.reasons


def test_preferred_contact_uses_documented_priority(rules: ScoringRules) -> None:
    result = score_company(
        score_input(
            status=WebsiteStatus.PARKED,
            contacts=(
                contact(ContactType.EMAIL, "sales@example.ru"),
                contact(ContactType.TELEGRAM, "nyra"),
                contact(ContactType.WHATSAPP, "+79991234567"),
                contact(ContactType.PHONE, "+79990000000"),
            ),
        ),
        rules,
        50,
    )

    assert result.preferred_contact_type is ContactType.PHONE
    assert result.preferred_contact_value == "+79990000000"
    assert result.contactability_score == 100


def test_invalid_rules_fail_fast(tmp_path: Path) -> None:
    rules_file = tmp_path / "invalid.toml"
    rules_file.write_text("[website]\nno_website = 101\n", encoding="utf-8")

    with pytest.raises(ConfigurationError):
        ScoringRules.load(rules_file)
