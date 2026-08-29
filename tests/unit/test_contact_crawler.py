from collections.abc import Sequence
from urllib.parse import urlsplit

from app.core.enums import ContactType, WebsiteStatus
from app.website_analyzer.checker import HTML_CONTENT_TYPES, WebsiteFetchResult
from app.website_analyzer.contacts import ContactCrawler


class FakeFetcher:
    def __init__(self, pages: dict[str, WebsiteFetchResult]) -> None:
        self.pages = pages
        self.requested_urls: list[str] = []

    async def fetch(
        self,
        website_url: str | None,
        *,
        accepted_content_types: Sequence[str] = HTML_CONTENT_TYPES,
    ) -> WebsiteFetchResult:
        assert website_url is not None
        self.requested_urls.append(website_url)
        return self.pages.get(
            website_url,
            WebsiteFetchResult(
                requested_url=website_url,
                final_url=website_url,
                status=WebsiteStatus.DEAD,
            ),
        )


def page(url: str, html: str) -> WebsiteFetchResult:
    return WebsiteFetchResult(
        requested_url=url,
        final_url=url,
        status=WebsiteStatus.ONLINE,
        http_status=200,
        is_https=urlsplit(url).scheme == "https",
        content_type="text/html",
        html=html,
    )


async def test_crawler_stays_same_origin_and_caps_pages() -> None:
    home_url = "https://example.ru/"
    links = "".join(f'<a href="/contact-{number}">Контакты {number}</a>' for number in range(10))
    pages = {
        "https://example.ru/robots.txt": page(
            "https://example.ru/robots.txt", "User-agent: *\nAllow: /"
        ),
        **{
            f"https://example.ru/contact-{number}": page(
                f"https://example.ru/contact-{number}",
                "Телефон +7 (999) 123-45-67",
            )
            for number in range(10)
        },
    }
    fetcher = FakeFetcher(pages)
    home = page(
        home_url,
        f'<a href="https://evil.test/contact">Контакты вне сайта</a>{links}',
    )

    contacts = await ContactCrawler(fetcher, max_pages=8).crawl(home)

    content_requests = [url for url in fetcher.requested_urls if not url.endswith("robots.txt")]
    assert len(content_requests) <= 7
    assert all(url.startswith("https://example.ru/") for url in fetcher.requested_urls)
    assert "+79991234567" in {item.normalized_value for item in contacts}
    assert len([url for url in content_requests if "contact-" in url]) <= 3


async def test_crawler_respects_robots_txt() -> None:
    home_url = "https://example.ru/"
    fetcher = FakeFetcher(
        {
            "https://example.ru/robots.txt": page(
                "https://example.ru/robots.txt",
                "User-agent: *\nDisallow: /contacts",
            ),
            "https://example.ru/about": page("https://example.ru/about", "Почта sales@example.ru"),
        }
    )

    contacts = await ContactCrawler(fetcher, max_pages=8).crawl(
        page(home_url, '<a href="/contacts">Наши контакты</a>')
    )

    assert "https://example.ru/contacts" not in fetcher.requested_urls
    assert "sales@example.ru" in {item.normalized_value for item in contacts}


async def test_crawler_reuses_home_and_includes_own_website() -> None:
    home_url = "https://example.ru/"
    fetcher = FakeFetcher({})

    contacts = await ContactCrawler(fetcher, max_pages=1).crawl(
        page(home_url, '<a href="mailto:hello@example.ru">Написать</a>')
    )

    values = {(item.type, item.normalized_value) for item in contacts}
    assert (ContactType.EMAIL, "hello@example.ru") in values
    assert (ContactType.WEBSITE, "example.ru") in values
    assert fetcher.requested_urls == ["https://example.ru/robots.txt"]
