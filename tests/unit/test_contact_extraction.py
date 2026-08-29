from app.core.enums import ContactType
from app.normalization.contacts import extract_contacts_from_html


def test_contact_extraction_finds_public_channels() -> None:
    html = """
    <a href="tel:89991234567">call</a>
    <a href="mailto:Sales@Example.ru">mail</a>
    <a href="https://t.me/nyra_test">tg</a>
    """

    contacts = extract_contacts_from_html(html, "https://example.ru")

    assert {(contact.type, contact.normalized_value) for contact in contacts} == {
        (ContactType.PHONE, "+79991234567"),
        (ContactType.EMAIL, "sales@example.ru"),
        (ContactType.TELEGRAM, "nyra_test"),
    }


def test_contact_extraction_deduplicates_text_and_href_phone() -> None:
    html = '<a href="tel:+79991234567">+7 (999) 123-45-67</a>'

    contacts = extract_contacts_from_html(html, "https://example.ru")

    phones = [item for item in contacts if item.type is ContactType.PHONE]
    assert len(phones) == 1


def test_contact_extraction_rejects_off_platform_lookalike_links() -> None:
    html = '<a href="https://evil.test/?next=https://t.me/not-a-contact">click</a>'

    contacts = extract_contacts_from_html(html, "https://example.ru")

    assert all(item.type is not ContactType.TELEGRAM for item in contacts)
