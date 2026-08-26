import json

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
