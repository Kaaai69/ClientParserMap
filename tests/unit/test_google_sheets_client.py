from typing import Any

from app.sheets.client import _execute


class TransientRequest:
    def execute(self, *, num_retries: int = 0) -> dict[str, Any]:
        if num_retries < 3:
            raise ConnectionError("transient Google Sheets transport failure")
        return {"status": "ok"}


async def test_execute_enables_google_client_retries_for_transient_failures() -> None:
    result = await _execute(TransientRequest())

    assert result == {"status": "ok"}
