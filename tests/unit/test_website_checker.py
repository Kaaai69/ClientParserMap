from collections.abc import Sequence

import httpx

from app.core.enums import WebsiteStatus
from app.website_analyzer.checker import WebsiteFetcher
from app.website_analyzer.security import SafeUrlPolicy


class PublicResolver:
    async def resolve(self, hostname: str) -> Sequence[str]:
        return ["93.184.216.34"]


def policy() -> SafeUrlPolicy:
    return SafeUrlPolicy(resolver=PublicResolver())


async def test_checker_revalidates_redirect_target() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": "http://127.0.0.1/admin"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await WebsiteFetcher(client, policy()).fetch("https://public.test")

    assert result.status is WebsiteStatus.ERROR
    assert result.error_code == "UNSAFE_REDIRECT"


async def test_404_is_dead() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="missing", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await WebsiteFetcher(client, policy()).fetch("https://missing.test")

    assert result.status is WebsiteStatus.DEAD
    assert result.http_status == 404


async def test_successful_redirect_records_final_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(301, headers={"Location": "/home"}, request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text="<html><body>Компания</body></html>",
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await WebsiteFetcher(client, policy()).fetch("http://public.test")

    assert result.status is WebsiteStatus.ONLINE
    assert result.final_url == "http://public.test/home"
    assert result.redirect_count == 1
    assert result.is_https is False
    assert result.html == "<html><body>Компания</body></html>"


async def test_timeout_has_stable_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await WebsiteFetcher(client, policy()).fetch("https://slow.test")

    assert result.status is WebsiteStatus.TIMEOUT
    assert result.error_code == "WEBSITE_TIMEOUT"


async def test_body_limit_is_enforced_while_streaming() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            content=b"x" * 101,
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await WebsiteFetcher(client, policy(), max_html_bytes=100).fetch("https://large.test")

    assert result.status is WebsiteStatus.ERROR
    assert result.error_code == "RESPONSE_TOO_LARGE"
    assert result.html is None


async def test_non_html_response_is_not_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF",
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await WebsiteFetcher(client, policy()).fetch("https://file.test")

    assert result.status is WebsiteStatus.ERROR
    assert result.error_code == "NON_HTML_RESPONSE"
    assert result.html is None


async def test_missing_website_skips_network() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await WebsiteFetcher(client, policy()).fetch(None)

    assert result.status is WebsiteStatus.NO_WEBSITE
    assert requests == 0


async def test_terminal_server_errors_are_retried_then_dead() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(503, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await WebsiteFetcher(client, policy(), server_error_attempts=2).fetch(
        "https://unavailable.test"
    )

    assert result.status is WebsiteStatus.DEAD
    assert result.error_code == "TERMINAL_SERVER_ERROR"
    assert requests == 2


async def test_rate_limit_is_retried_then_succeeds() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="<html>Компания</html>",
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await WebsiteFetcher(client, policy(), server_error_attempts=2).fetch(
        "https://limited.test"
    )

    assert result.status is WebsiteStatus.ONLINE
    assert requests == 2
