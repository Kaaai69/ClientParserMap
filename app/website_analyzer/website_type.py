from dataclasses import dataclass
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.core.enums import WebsiteStatus, WebsiteType

BUSINESS_CARD_HOST_SUFFIXES = (
    "clients.site",
    "taplink.cc",
    "hipolink.me",
    "linktr.ee",
    "mssg.me",
)
PARKING_HOST_MARKERS = ("parking.", "parked.", "sedoparking.")
PARKING_CONTENT_MARKERS = (
    "domain is parked",
    "домен припаркован",
    "buy this domain",
    "купить этот домен",
    "this domain is for sale",
    "sedoparking",
)
PLACEHOLDER_MARKERS = (
    "сайт в разработке",
    "сайт находится в разработке",
    "скоро открытие",
    "under construction",
    "coming soon",
    "default web site page",
)


@dataclass(frozen=True, slots=True)
class WebsiteClassification:
    status: WebsiteStatus
    website_type: WebsiteType
    reasons: tuple[str, ...] = ()


def classify_website(url: str, html: str) -> WebsiteClassification:
    hostname = (urlsplit(url).hostname or "").casefold().rstrip(".")
    content = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).casefold()
    is_card = any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in BUSINESS_CARD_HOST_SUFFIXES
    )
    website_type = WebsiteType.BUSINESS_CARD if is_card else WebsiteType.NORMAL

    parking_evidence = [marker for marker in PARKING_CONTENT_MARKERS if marker in content]
    parked_host = any(marker in hostname for marker in PARKING_HOST_MARKERS)
    if parked_host or len(parking_evidence) >= 2:
        reasons = (["parking-host"] if parked_host else []) + parking_evidence
        return WebsiteClassification(
            status=WebsiteStatus.PARKED,
            website_type=website_type,
            reasons=tuple(reasons),
        )

    placeholder_evidence = [marker for marker in PLACEHOLDER_MARKERS if marker in content]
    word_count = len(content.split())
    if placeholder_evidence and word_count < 80:
        return WebsiteClassification(
            status=WebsiteStatus.PLACEHOLDER,
            website_type=website_type,
            reasons=tuple(placeholder_evidence),
        )
    return WebsiteClassification(
        status=WebsiteStatus.ONLINE,
        website_type=website_type,
    )
