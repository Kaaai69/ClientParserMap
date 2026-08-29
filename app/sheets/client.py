import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.core.config import Settings
from app.core.errors import ConfigurationError

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
GOOGLE_API_NUM_RETRIES = 3


@dataclass(frozen=True, slots=True)
class SheetRow:
    row_number: int
    values: dict[str, str]


class SheetsClient(Protocol):
    async def ensure_worksheet(self, name: str, headers: Sequence[str]) -> None: ...

    async def read_records(self, name: str) -> list[SheetRow]: ...

    async def read_row(
        self,
        name: str,
        row_number: int,
        headers: Sequence[str],
    ) -> SheetRow: ...

    async def append_row(
        self,
        name: str,
        headers: Sequence[str],
        values: dict[str, str],
    ) -> int: ...

    async def update_row(
        self,
        name: str,
        row_number: int,
        headers: Sequence[str],
        values: dict[str, str],
    ) -> None: ...


class GoogleSheetsClient:
    """Async facade over the official blocking Google Sheets API client."""

    def __init__(self, service: Any, spreadsheet_id: str) -> None:
        self._service = service
        self._spreadsheet_id = spreadsheet_id

    @classmethod
    async def create(cls, settings: Settings) -> "GoogleSheetsClient":
        if not settings.google_sheets_spreadsheet_id:
            raise ConfigurationError("Не задан ID Google-таблицы")
        if settings.google_service_account_file is None:
            raise ConfigurationError("Не задан файл сервисного аккаунта Google")

        def create_service() -> Any:
            credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                str(settings.google_service_account_file),
                scopes=[SHEETS_SCOPE],
            )
            return build(
                "sheets",
                "v4",
                credentials=credentials,
                cache_discovery=False,
            )

        service = await asyncio.to_thread(create_service)
        return cls(service, settings.google_sheets_spreadsheet_id)

    async def ensure_worksheet(self, name: str, headers: Sequence[str]) -> None:
        metadata_request = self._service.spreadsheets().get(
            spreadsheetId=self._spreadsheet_id,
            fields="sheets.properties.title",
        )
        metadata = await _execute(metadata_request)
        titles = {sheet.get("properties", {}).get("title") for sheet in metadata.get("sheets", [])}
        if name not in titles:
            add_request = self._service.spreadsheets().batchUpdate(
                spreadsheetId=self._spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": name}}}]},
            )
            await _execute(add_request)

        header_range = f"{_quote_title(name)}!A1:{_column_name(len(headers))}1"
        read_request = (
            self._service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self._spreadsheet_id,
                range=header_range,
            )
        )
        current = await _execute(read_request)
        rows = current.get("values", [])
        current_headers = tuple(str(value) for value in rows[0]) if rows else ()
        if current_headers != tuple(headers):
            update_request = (
                self._service.spreadsheets()
                .values()
                .update(
                    spreadsheetId=self._spreadsheet_id,
                    range=header_range,
                    valueInputOption="RAW",
                    body={"values": [list(headers)]},
                )
            )
            await _execute(update_request)

    async def read_records(self, name: str) -> list[SheetRow]:
        request = (
            self._service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self._spreadsheet_id,
                range=_quote_title(name),
            )
        )
        payload = await _execute(request)
        rows = payload.get("values", [])
        if not rows:
            return []
        headers = [str(value) for value in rows[0]]
        records: list[SheetRow] = []
        for row_number, raw_row in enumerate(rows[1:], start=2):
            values = {
                header: str(raw_row[index]) if index < len(raw_row) else ""
                for index, header in enumerate(headers)
            }
            records.append(SheetRow(row_number=row_number, values=values))
        return records

    async def read_row(
        self,
        name: str,
        row_number: int,
        headers: Sequence[str],
    ) -> SheetRow:
        row_range = f"{_quote_title(name)}!A{row_number}:{_column_name(len(headers))}{row_number}"
        request = (
            self._service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self._spreadsheet_id,
                range=row_range,
            )
        )
        payload = await _execute(request)
        rows = payload.get("values", [])
        raw_row = rows[0] if rows else []
        values = {
            header: str(raw_row[index]) if index < len(raw_row) else ""
            for index, header in enumerate(headers)
        }
        return SheetRow(row_number=row_number, values=values)

    async def append_row(
        self,
        name: str,
        headers: Sequence[str],
        values: dict[str, str],
    ) -> int:
        request = (
            self._service.spreadsheets()
            .values()
            .append(
                spreadsheetId=self._spreadsheet_id,
                range=f"{_quote_title(name)}!A:A",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [[values.get(header, "") for header in headers]]},
            )
        )
        payload = await _execute(request)
        updated_range = str(payload.get("updates", {}).get("updatedRange", ""))
        match = re.search(r"!A(\d+)(?::|$)", updated_range)
        if match is None:
            raise RuntimeError("Google Sheets did not return the appended row number")
        return int(match.group(1))

    async def update_row(
        self,
        name: str,
        row_number: int,
        headers: Sequence[str],
        values: dict[str, str],
    ) -> None:
        row_range = f"{_quote_title(name)}!A{row_number}:{_column_name(len(headers))}{row_number}"
        request = (
            self._service.spreadsheets()
            .values()
            .update(
                spreadsheetId=self._spreadsheet_id,
                range=row_range,
                valueInputOption="RAW",
                body={"values": [[values.get(header, "") for header in headers]]},
            )
        )
        await _execute(request)


async def _execute(request: Any) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        await asyncio.to_thread(request.execute, num_retries=GOOGLE_API_NUM_RETRIES),
    )


def _quote_title(title: str) -> str:
    return f"'{title.replace("'", "''")}'"


def _column_name(column_count: int) -> str:
    if column_count < 1:
        raise ValueError("column_count must be positive")
    value = column_count
    letters = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
