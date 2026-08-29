from collections.abc import Iterable, Sequence
from typing import cast

import httpcore
import pytest

from app.core.errors import UnsafeTargetError
from app.website_analyzer.security import PinnedNetworkBackend, SafeUrlPolicy


class FakeResolver:
    def __init__(self, addresses: Sequence[str]) -> None:
        self.addresses = addresses
        self.hostnames: list[str] = []

    async def resolve(self, hostname: str) -> Sequence[str]:
        self.hostnames.append(hostname)
        return self.addresses


@pytest.mark.parametrize(
    "ip_address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "224.0.0.1"],
)
async def test_policy_rejects_non_public_dns(ip_address: str) -> None:
    policy = SafeUrlPolicy(resolver=FakeResolver([ip_address]))

    with pytest.raises(UnsafeTargetError):
        await policy.validate("https://example.test")


async def test_policy_accepts_public_dns_and_normalizes_url() -> None:
    resolver = FakeResolver(["93.184.216.34"])
    policy = SafeUrlPolicy(resolver=resolver)

    result = await policy.validate("example.test/contact#team")

    assert result == "https://example.test/contact"
    assert resolver.hostnames == ["example.test"]


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.test/file",
        "https://user:password@example.test",
        "https://127.0.0.1/admin",
        "http://[::1]/admin",
    ],
)
async def test_policy_rejects_unsafe_url_forms(url: str) -> None:
    policy = SafeUrlPolicy(resolver=FakeResolver(["93.184.216.34"]))

    with pytest.raises(UnsafeTargetError):
        await policy.validate(url)


async def test_policy_rejects_empty_dns_answer() -> None:
    policy = SafeUrlPolicy(resolver=FakeResolver([]))

    with pytest.raises(UnsafeTargetError) as error:
        await policy.validate("https://missing.test")

    assert error.value.code == "DNS_RESOLUTION_FAILED"


async def test_network_backend_connects_only_to_prevalidated_ip() -> None:
    class RecordingBackend(httpcore.AsyncNetworkBackend):
        def __init__(self) -> None:
            self.hosts: list[str] = []

        async def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,  # noqa: ASYNC109 - httpcore interface
            local_address: str | None = None,
            socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
        ) -> httpcore.AsyncNetworkStream:
            self.hosts.append(host)
            return cast(httpcore.AsyncNetworkStream, object())

        async def connect_unix_socket(
            self,
            path: str,
            timeout: float | None = None,  # noqa: ASYNC109 - httpcore interface
            socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
        ) -> httpcore.AsyncNetworkStream:
            raise AssertionError("Unix sockets are not allowed")

        async def sleep(self, seconds: float) -> None:
            return None

    policy = SafeUrlPolicy(resolver=FakeResolver(["93.184.216.34"]))
    await policy.validate("https://example.test")
    recording = RecordingBackend()
    backend = PinnedNetworkBackend(policy, backend=recording)

    await backend.connect_tcp("example.test", 443)

    assert recording.hosts == ["93.184.216.34"]
