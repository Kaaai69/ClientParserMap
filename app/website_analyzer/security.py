import asyncio
import ipaddress
import socket
from collections.abc import Iterable, Sequence
from typing import Protocol
from urllib.parse import urlsplit

import httpcore
import httpx

from app.core.errors import UnsafeTargetError
from app.normalization.urls import normalize_url


class HostResolver(Protocol):
    async def resolve(self, hostname: str) -> Sequence[str]: ...


class SystemResolver:
    async def resolve(self, hostname: str) -> Sequence[str]:
        loop = asyncio.get_running_loop()
        records = await loop.getaddrinfo(
            hostname,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
        return tuple(dict.fromkeys(record[4][0] for record in records))


class SafeUrlPolicy:
    """Reject URL targets that may reach local or special-purpose networks."""

    def __init__(self, resolver: HostResolver | None = None) -> None:
        self._resolver = resolver or SystemResolver()
        self._approved_addresses: dict[str, tuple[str, ...]] = {}

    async def validate(self, url: str) -> str:
        normalized = normalize_url(url)
        if normalized is None:
            raise UnsafeTargetError("INVALID_WEBSITE_URL")
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        if hostname is None:
            raise UnsafeTargetError("INVALID_WEBSITE_URL")

        try:
            literal_address = ipaddress.ip_address(hostname)
        except ValueError:
            try:
                addresses = await self._resolver.resolve(hostname)
            except (OSError, UnicodeError) as error:
                raise UnsafeTargetError("DNS_RESOLUTION_FAILED") from error
            if not addresses:
                raise UnsafeTargetError("DNS_RESOLUTION_FAILED") from None
        else:
            addresses = (str(literal_address),)

        validated_addresses: list[str] = []
        for raw_address in addresses:
            try:
                address = ipaddress.ip_address(raw_address)
            except ValueError as error:
                raise UnsafeTargetError("DNS_RESOLUTION_FAILED") from error
            if (
                not address.is_global
                or address.is_loopback
                or address.is_private
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
                or address.is_unspecified
            ):
                raise UnsafeTargetError("UNSAFE_TARGET")
            validated_addresses.append(str(address))
        self._approved_addresses[hostname] = tuple(validated_addresses)
        return normalized

    def approved_addresses(self, hostname: str) -> tuple[str, ...]:
        return self._approved_addresses.get(hostname.casefold().rstrip("."), ())


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect to the exact public IP approved before the HTTP request."""

    def __init__(
        self,
        policy: SafeUrlPolicy,
        *,
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._policy = policy
        self._backend = backend or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore interface
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = self._policy.approved_addresses(host)
        if not addresses:
            raise OSError("target was not approved before connection")
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as error:
                last_error = error
        raise OSError("all approved target addresses failed") from last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore interface
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise OSError("Unix sockets are forbidden for website analysis")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """httpx transport whose network layer cannot perform a second DNS lookup."""

    def __init__(self, policy: SafeUrlPolicy, *, limits: httpx.Limits) -> None:
        ssl_context = httpx.create_ssl_context(verify=True, trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=PinnedNetworkBackend(policy),
        )
