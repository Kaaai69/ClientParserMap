from typing import Protocol

from app.core.enums import SourceName
from app.schemas.domain import SearchCriteria, SourcePage


class LeadSource(Protocol):
    """A paginated official source of company data."""

    name: SourceName

    async def search_page(
        self,
        criteria: SearchCriteria,
        cursor: str | None,
    ) -> SourcePage: ...

    async def aclose(self) -> None: ...
