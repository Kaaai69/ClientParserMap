import re
from collections.abc import Iterable
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from app.core.enums import ContactType
from app.normalization.phones import normalize_phone
from app.normalization.text import normalize_address, normalize_name
from app.normalization.urls import normalize_url, registrable_domain
from app.schemas.domain import ContactValue, NormalizedCompany, SourceCompany

EMAIL_RE = re.compile(r"(?<![\w.+-])([\w.+-]+@[\w-]+(?:\.[\w-]+)+)", re.IGNORECASE)
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"
)
SOCIAL_HOSTS = {
    "t.me": ContactType.TELEGRAM,
    "telegram.me": ContactType.TELEGRAM,
    "wa.me": ContactType.WHATSAPP,
    "api.whatsapp.com": ContactType.WHATSAPP,
    "vk.com": ContactType.VK,
    "www.vk.com": ContactType.VK,
    "instagram.com": ContactType.INSTAGRAM,
    "www.instagram.com": ContactType.INSTAGRAM,
}


def normalize_email(value: str) -> str | None:
    candidate = unquote(value).strip().strip('.,;:()[]<>"').casefold()
    return candidate if EMAIL_RE.fullmatch(candidate) else None


def normalize_social_url(value: str) -> ContactValue | None:
    normalized_url = normalize_url(value)
    if normalized_url is None:
        return None
    parsed = urlsplit(normalized_url)
    contact_type = SOCIAL_HOSTS.get(parsed.hostname or "")
    if contact_type is None:
        return None

    if contact_type is ContactType.WHATSAPP:
        if parsed.hostname == "api.whatsapp.com":
            query_match = re.search(r"(?:^|&)phone=([^&]+)", parsed.query)
            raw_phone = unquote(query_match.group(1)) if query_match else ""
        else:
            raw_phone = parsed.path.strip("/").split("/", maxsplit=1)[0]
        phone = normalize_phone(raw_phone)
        if phone is None:
            return None
        return ContactValue(
            type=contact_type,
            value=f"https://wa.me/{phone.lstrip('+')}",
            normalized_value=phone,
        )

    handle = parsed.path.strip("/").split("/", maxsplit=1)[0].lstrip("@").casefold()
    if not handle or handle in {"share", "login", "accounts", "explore"}:
        return None
    canonical_host = {
        ContactType.TELEGRAM: "t.me",
        ContactType.VK: "vk.com",
        ContactType.INSTAGRAM: "instagram.com",
    }[contact_type]
    return ContactValue(
        type=contact_type,
        value=f"https://{canonical_host}/{handle}",
        normalized_value=handle,
    )


def normalize_contact(contact_type: ContactType, value: str) -> ContactValue | None:
    if contact_type is ContactType.PHONE:
        normalized = normalize_phone(value)
        if normalized is None:
            return None
        return ContactValue(type=contact_type, value=normalized, normalized_value=normalized)
    if contact_type is ContactType.EMAIL:
        normalized = normalize_email(value)
        if normalized is None:
            return None
        return ContactValue(type=contact_type, value=normalized, normalized_value=normalized)
    if contact_type is ContactType.WEBSITE:
        normalized = normalize_url(value)
        domain = registrable_domain(value)
        if normalized is None or domain is None:
            return None
        return ContactValue(type=contact_type, value=normalized, normalized_value=domain)
    if contact_type in {
        ContactType.TELEGRAM,
        ContactType.WHATSAPP,
        ContactType.VK,
        ContactType.INSTAGRAM,
    }:
        normalized_social = normalize_social_url(value)
        if normalized_social is None or normalized_social.type is not contact_type:
            return None
        return normalized_social
    normalized_url = normalize_url(value)
    if normalized_url is None:
        return None
    return ContactValue(type=contact_type, value=normalized_url, normalized_value=normalized_url)


def _deduplicate_contacts(contacts: Iterable[ContactValue]) -> tuple[ContactValue, ...]:
    unique: dict[tuple[ContactType, str], ContactValue] = {}
    for contact in contacts:
        key = (contact.type, contact.normalized_value)
        previous = unique.get(key)
        if previous is None or (contact.is_primary and not previous.is_primary):
            unique[key] = contact
    return tuple(unique.values())


def normalize_source_company(record: SourceCompany) -> NormalizedCompany:
    contacts: list[ContactValue] = []
    primary_normalized = normalize_phone(record.primary_phone) if record.primary_phone else None
    for phone in record.phones + ((record.primary_phone,) if record.primary_phone else ()):
        contact = normalize_contact(ContactType.PHONE, phone)
        if contact is not None:
            contacts.append(
                contact.model_copy(
                    update={"is_primary": contact.normalized_value == primary_normalized}
                )
            )

    source_groups = (
        (ContactType.EMAIL, record.emails),
        (ContactType.WEBSITE, record.websites),
        (ContactType.TELEGRAM, record.telegram),
        (ContactType.WHATSAPP, record.whatsapp),
        (ContactType.VK, record.vk),
        (ContactType.INSTAGRAM, record.instagram),
        (ContactType.OTHER, record.other_socials),
    )
    for contact_type, values in source_groups:
        contacts.extend(
            contact
            for value in values
            if (contact := normalize_contact(contact_type, value)) is not None
        )

    websites = tuple(
        dict.fromkeys(
            normalized
            for value in record.websites
            if (normalized := normalize_url(value)) is not None
        )
    )
    domains = tuple(
        dict.fromkeys(
            domain for value in websites if (domain := registrable_domain(value)) is not None
        )
    )
    normalized_contacts = _deduplicate_contacts(contacts)
    return NormalizedCompany(
        name=normalize_name(record.name),
        address=normalize_address(record.address),
        phone_numbers=tuple(
            contact.normalized_value
            for contact in normalized_contacts
            if contact.type is ContactType.PHONE
        ),
        websites=websites,
        domains=domains,
        contacts=normalized_contacts,
    )


def extract_contacts_from_html(html: str, base_url: str) -> tuple[ContactValue, ...]:
    soup = BeautifulSoup(html, "html.parser")
    contacts: list[ContactValue] = []

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        lowered = href.casefold()
        if lowered.startswith("tel:"):
            contact = normalize_contact(ContactType.PHONE, unquote(href[4:]))
        elif lowered.startswith("mailto:"):
            contact = normalize_contact(ContactType.EMAIL, unquote(href[7:].split("?", 1)[0]))
        else:
            absolute = urljoin(base_url, href)
            contact = normalize_social_url(absolute)
        if contact is not None:
            contacts.append(contact)

    visible_text = soup.get_text(" ", strip=True)
    for email in EMAIL_RE.findall(visible_text):
        contact = normalize_contact(ContactType.EMAIL, email)
        if contact is not None:
            contacts.append(contact)
    for phone in PHONE_RE.findall(visible_text):
        contact = normalize_contact(ContactType.PHONE, phone)
        if contact is not None:
            contacts.append(contact)

    return _deduplicate_contacts(contacts)
