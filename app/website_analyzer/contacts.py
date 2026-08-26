from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from app.core.enums import ContactType, WebsiteStatus
from app.normalization.contacts import extract_contacts_from_html, normalize_contact
from app.normalization.urls import normalize_url
from app.schemas.domain import ContactValue
from app.website_analyzer.checker import HTML_CONTENT_TYPES, WebsiteFetchResult

CONTACT_PATHS = ("/contact", "/contacts", "/kontakty", "/about")
CONTACT_HINTS = ("contact", "kontakt", "контакт", "связ", "about", "о-компании")
ROBOTS_CONTENT_TYPES = ("text/plain", "text/html", "application/xhtml+xml")


class PageFetcher(Protocol):
    async def fetch(
        self,
        website_url: str | None,
        *,
        accepted_content_types: Sequence[str] = HTML_CONTENT_TYPES,
    ) -> WebsiteFetchResult: ...


class ContactCrawler:
    """Sequential same-origin contact crawl with a strict HTML page budget."""

    def __init__(
        self,
        fetcher: PageFetcher,
        *,
        max_pages: int = 8,
        max_discovered_links: int = 3,
        user_agent: str = "ClientParserMap",
    ) -> None:
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if max_discovered_links < 0:
            raise ValueError("max_discovered_links must not be negative")
        self._fetcher = fetcher
        self._max_pages = max_pages
        self._max_discovered_links = max_discovered_links
        self._user_agent = user_agent

    async def crawl(self, home: WebsiteFetchResult) -> tuple[ContactValue, ...]:
        if (
            home.status is not WebsiteStatus.ONLINE
            or home.final_url is None
            or home.html is None
        ):
            return ()
        base_url = home.final_url
        origin = _origin(base_url)
        if origin is None:
            return ()

        contacts = list(extract_contacts_from_html(home.html, base_url))
        website_contact = normalize_contact(ContactType.WEBSITE, base_url)
        if website_contact is not None:
            contacts.append(website_contact)

        robots = await self._load_robots(origin)
        conventional = [urljoin(origin, path) for path in CONTACT_PATHS]
        discovered = _contact_links(
            home.html,
            base_url,
            origin,
            limit=self._max_discovered_links,
        )
        candidates = tuple(dict.fromkeys(conventional + list(discovered)))
        remaining_pages = self._max_pages - 1
        for candidate in candidates:
            if remaining_pages <= 0:
                break
            if not robots.can_fetch(self._user_agent, candidate):
                continue
            remaining_pages -= 1
            page = await self._fetcher.fetch(candidate)
            if (
                page.status is WebsiteStatus.ONLINE
                and page.final_url is not None
                and page.html is not None
                and _origin(page.final_url) == origin
            ):
                contacts.extend(extract_contacts_from_html(page.html, page.final_url))
        return _deduplicate(contacts)

    async def _load_robots(self, origin: str) -> RobotFileParser:
        parser = RobotFileParser()
        robots_url = urljoin(origin, "/robots.txt")
        parser.set_url(robots_url)
        result = await self._fetcher.fetch(
            robots_url,
            accepted_content_types=ROBOTS_CONTENT_TYPES,
        )
        if result.status is WebsiteStatus.ONLINE and result.html is not None:
            parser.parse(result.html.splitlines())
        else:
            parser.parse(())
        return parser


def _origin(url: str) -> str | None:
    normalized = normalize_url(url)
    if normalized is None:
        return None
    parsed = urlsplit(normalized)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/"


def _contact_links(
    html: str,
    base_url: str,
    origin: str,
    *,
    limit: int,
) -> tuple[str, ...]:
    if limit == 0:
        return ()
    links: list[str] = []
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        hint_text = f"{href} {anchor.get_text(' ', strip=True)}".casefold()
        if not any(hint in hint_text for hint in CONTACT_HINTS):
            continue
        absolute = normalize_url(urljoin(base_url, href))
        if absolute is None or _origin(absolute) != origin or absolute in links:
            continue
        links.append(absolute)
        if len(links) == limit:
            break
    return tuple(links)


def _deduplicate(contacts: Sequence[ContactValue]) -> tuple[ContactValue, ...]:
    unique: dict[tuple[ContactType, str], ContactValue] = {}
    for contact in contacts:
        unique[(contact.type, contact.normalized_value)] = contact
    return tuple(unique.values())
