import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.enums import WebsiteStatus
from app.core.errors import UnsafeTargetError
from app.website_analyzer.security import SafeUrlPolicy

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
DEAD_STATUSES = frozenset({404, 410})
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")


@dataclass(frozen=True, slots=True)
class WebsiteFetchResult:
    requested_url: str | None
    final_url: str | None
    status: WebsiteStatus
    http_status: int | None = None
    is_https: bool = False
    redirect_count: int = 0
    response_time_ms: int | None = None
    content_type: str | None = None
    html: str | None = None
    headers: tuple[tuple[str, str], ...] = ()
    error_code: str | None = None


class WebsiteFetcher:
    """Fetch website HTML with manual redirects and strict resource bounds."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        policy: SafeUrlPolicy,
        *,
        max_redirects: int = 5,
        max_html_bytes: int = 5_000_000,
        server_error_attempts: int = 2,
        user_agent: str = "ClientParserMap/0.1 (+website-check)",
    ) -> None:
        if max_redirects < 0:
            raise ValueError("max_redirects must not be negative")
        if max_html_bytes < 1:
            raise ValueError("max_html_bytes must be positive")
        if server_error_attempts < 1:
            raise ValueError("server_error_attempts must be at least 1")
        self._client = client
        self._policy = policy
        self._max_redirects = max_redirects
        self._max_html_bytes = max_html_bytes
        self._server_error_attempts = server_error_attempts
        self._headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": user_agent,
        }

    async def fetch(self, website_url: str | None) -> WebsiteFetchResult:
        if website_url is None or not website_url.strip():
            return WebsiteFetchResult(
                requested_url=website_url,
                final_url=None,
                status=WebsiteStatus.NO_WEBSITE,
            )

        started = time.monotonic()
        try:
            requested_url = await self._policy.validate(website_url)
        except UnsafeTargetError as error:
            return self._failure(
                website_url,
                None,
                WebsiteStatus.DEAD
                if error.code == "DNS_RESOLUTION_FAILED"
                else WebsiteStatus.ERROR,
                started,
                error.code,
            )

        current_url = requested_url
        visited: set[str] = set()
        redirects = 0
        server_error_count = 0
        while True:
            if current_url in visited:
                return self._failure(
                    requested_url,
                    current_url,
                    WebsiteStatus.DEAD,
                    started,
                    "REDIRECT_LOOP",
                    redirect_count=redirects,
                )
            visited.add(current_url)
            try:
                response = await self._send(current_url)
            except httpx.TimeoutException:
                return self._failure(
                    requested_url,
                    current_url,
                    WebsiteStatus.TIMEOUT,
                    started,
                    "WEBSITE_TIMEOUT",
                    redirect_count=redirects,
                )
            except httpx.HTTPError:
                return self._failure(
                    requested_url,
                    current_url,
                    WebsiteStatus.DEAD,
                    started,
                    "WEBSITE_CONNECTION_ERROR",
                    redirect_count=redirects,
                )

            try:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location or redirects >= self._max_redirects:
                        return self._response_failure(
                            requested_url,
                            current_url,
                            response,
                            WebsiteStatus.DEAD,
                            started,
                            "INVALID_REDIRECT_CHAIN",
                            redirects,
                        )
                    candidate = urljoin(current_url, location)
                    try:
                        current_url = await self._policy.validate(candidate)
                    except UnsafeTargetError:
                        return self._response_failure(
                            requested_url,
                            candidate,
                            response,
                            WebsiteStatus.ERROR,
                            started,
                            "UNSAFE_REDIRECT",
                            redirects + 1,
                        )
                    redirects += 1
                    server_error_count = 0
                    continue

                if response.status_code in DEAD_STATUSES:
                    return self._response_failure(
                        requested_url,
                        current_url,
                        response,
                        WebsiteStatus.DEAD,
                        started,
                        f"HTTP_{response.status_code}",
                        redirects,
                    )

                if response.status_code >= 500:
                    server_error_count += 1
                    if server_error_count < self._server_error_attempts:
                        visited.discard(current_url)
                        continue
                    return self._response_failure(
                        requested_url,
                        current_url,
                        response,
                        WebsiteStatus.DEAD,
                        started,
                        "TERMINAL_SERVER_ERROR",
                        redirects,
                    )

                if response.status_code >= 400:
                    return self._response_failure(
                        requested_url,
                        current_url,
                        response,
                        WebsiteStatus.ERROR,
                        started,
                        f"HTTP_{response.status_code}",
                        redirects,
                    )

                content_type = response.headers.get("Content-Type", "").casefold()
                if not content_type.startswith(HTML_CONTENT_TYPES):
                    return self._response_failure(
                        requested_url,
                        current_url,
                        response,
                        WebsiteStatus.ERROR,
                        started,
                        "NON_HTML_RESPONSE",
                        redirects,
                    )
                body = await self._read_bounded(response)
                if body is None:
                    return self._response_failure(
                        requested_url,
                        current_url,
                        response,
                        WebsiteStatus.ERROR,
                        started,
                        "RESPONSE_TOO_LARGE",
                        redirects,
                    )
                encoding = response.encoding or "utf-8"
                return WebsiteFetchResult(
                    requested_url=requested_url,
                    final_url=current_url,
                    status=WebsiteStatus.ONLINE,
                    http_status=response.status_code,
                    is_https=urlsplit(current_url).scheme == "https",
                    redirect_count=redirects,
                    response_time_ms=_elapsed_ms(started),
                    content_type=response.headers.get("Content-Type"),
                    html=body.decode(encoding, errors="replace"),
                    headers=tuple(response.headers.multi_items()),
                )
            finally:
                await response.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _send(self, url: str) -> httpx.Response:
        request = self._client.build_request("GET", url, headers=self._headers)
        return await self._client.send(request, stream=True, follow_redirects=False)

    async def _read_bounded(self, response: httpx.Response) -> bytes | None:
        raw_content_length = response.headers.get("Content-Length")
        if raw_content_length:
            try:
                if int(raw_content_length) > self._max_html_bytes:
                    return None
            except ValueError:
                pass
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > self._max_html_bytes:
                return None
        return bytes(content)

    def _response_failure(
        self,
        requested_url: str,
        final_url: str,
        response: httpx.Response,
        status: WebsiteStatus,
        started: float,
        error_code: str,
        redirect_count: int,
    ) -> WebsiteFetchResult:
        return WebsiteFetchResult(
            requested_url=requested_url,
            final_url=final_url,
            status=status,
            http_status=response.status_code,
            is_https=urlsplit(final_url).scheme == "https",
            redirect_count=redirect_count,
            response_time_ms=_elapsed_ms(started),
            content_type=response.headers.get("Content-Type"),
            headers=tuple(response.headers.multi_items()),
            error_code=error_code,
        )

    @staticmethod
    def _failure(
        requested_url: str | None,
        final_url: str | None,
        status: WebsiteStatus,
        started: float,
        error_code: str,
        *,
        redirect_count: int = 0,
    ) -> WebsiteFetchResult:
        return WebsiteFetchResult(
            requested_url=requested_url,
            final_url=final_url,
            status=status,
            is_https=bool(final_url and urlsplit(final_url).scheme == "https"),
            redirect_count=redirect_count,
            response_time_ms=_elapsed_ms(started),
            error_code=error_code,
        )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))
