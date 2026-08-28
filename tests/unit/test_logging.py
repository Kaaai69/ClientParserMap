import json

import httpx
import pytest

from app.core.logging import configure_logging, get_logger


def test_structured_log_redacts_secrets_and_contacts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")

    get_logger().info(
        "source_failed",
        api_key="secret-key",
        phone="+79991234567",
        nested={"email": "sales@example.ru"},
        error_code="SOURCE_TIMEOUT",
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert "secret-key" not in output
    assert "+79991234567" not in output
    assert "sales@example.ru" not in output
    assert payload["error_code"] == "SOURCE_TIMEOUT"
    assert payload["event"] == "source_failed"


async def test_httpx_info_log_does_not_expose_query_secrets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    sentinel_value = "unique-sensitive-value"

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))
    ) as client:
        await client.get("https://source.test/items", params={"key": sentinel_value})

    output = capsys.readouterr().out
    assert sentinel_value not in output
