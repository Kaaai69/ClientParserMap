import asyncio
import ipaddress
import socket
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urlsplit

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
        return normalized
