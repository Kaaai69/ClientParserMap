import pytest

from app.core.enums import ContactType, SourceName
from app.normalization.contacts import normalize_source_company
from app.normalization.phones import normalize_phone
from app.normalization.text import normalize_address, normalize_name
from app.normalization.urls import normalize_url, registrable_domain
from app.schemas.domain import SourceCompany


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8 (999) 123-45-67", "+79991234567"),
        ("+7 999 123 45 67", "+79991234567"),
        ("79991234567", "+79991234567"),
    ],
)
def test_normalize_russian_phone(raw: str, expected: str) -> None:
    assert normalize_phone(raw, "RU") == expected


def test_invalid_phone_is_rejected() -> None:
    assert normalize_phone("123", "RU") is None


def test_normalize_url_removes_tracking_and_fragment() -> None:
    assert normalize_url("Example.RU/path/?utm_source=x#top") == "https://example.ru/path/"


def test_url_with_credentials_is_rejected() -> None:
    assert normalize_url("https://admin:secret@example.ru") is None


def test_registrable_domain_handles_subdomains() -> None:
    assert registrable_domain("https://shop.example.co.uk/path") == "example.co.uk"


def test_text_normalization_is_unicode_and_whitespace_stable() -> None:
    assert normalize_name("  ООО «АВТО—Детейлинг» ") == "ооо авто детейлинг"
    assert normalize_address("Москва,  ул. Тверская,  10") == "москва ул тверская 10"


def test_source_company_normalization_deduplicates_equivalent_contacts() -> None:
    normalized = normalize_source_company(
        SourceCompany(
            source=SourceName.GOOGLE,
            source_id="g-1",
            name="Test",
            city="Москва",
            primary_phone="+7 999 123-45-67",
            phones=("89991234567", "+79991234567"),
            websites=("example.ru", "https://www.example.ru/"),
        )
    )

    phone_contacts = [item for item in normalized.contacts if item.type is ContactType.PHONE]
    assert len(phone_contacts) == 1
    assert phone_contacts[0].normalized_value == "+79991234567"
    assert phone_contacts[0].is_primary is True
    assert normalized.domains == ("example.ru",)
