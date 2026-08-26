from app.core.enums import WebsiteStatus, WebsiteType
from app.website_analyzer.website_type import classify_website


def test_clients_site_is_business_card() -> None:
    result = classify_website("https://shop.clients.site", "<html></html>")

    assert result.website_type is WebsiteType.BUSINESS_CARD
    assert result.status is WebsiteStatus.ONLINE


def test_multiple_parking_signals_take_precedence() -> None:
    html = "<html><body>This domain is parked. Buy this domain now.</body></html>"

    result = classify_website("https://example.ru", html)

    assert result.status is WebsiteStatus.PARKED


def test_placeholder_requires_thin_content() -> None:
    result = classify_website(
        "https://example.ru",
        "<html><body><h1>Сайт в разработке</h1><p>Скоро открытие</p></body></html>",
    )

    assert result.status is WebsiteStatus.PLACEHOLDER


def test_substantive_site_is_online() -> None:
    text = " ".join(f"Услуга {number}" for number in range(100))

    result = classify_website("https://example.ru", f"<html><body>{text}</body></html>")

    assert result.status is WebsiteStatus.ONLINE
    assert result.website_type is WebsiteType.NORMAL
