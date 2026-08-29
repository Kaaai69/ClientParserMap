import pytest
from pydantic import ValidationError

from app.core.enums import SourceName
from app.schemas.domain import SearchCriteria, SourceCompany


def test_search_criteria_rejects_empty_query() -> None:
    with pytest.raises(ValidationError):
        SearchCriteria(city="Москва", query=" ", max_results=100)


def test_search_criteria_strips_text_and_accepts_optional_filters() -> None:
    criteria = SearchCriteria(
        city="  Москва ",
        query=" детейлинг  ",
        min_rating=None,
        min_reviews=None,
        max_results=300,
    )

    assert criteria.city == "Москва"
    assert criteria.query == "детейлинг"


def test_source_company_keeps_all_contacts() -> None:
    company = SourceCompany(
        source=SourceName.GOOGLE,
        source_id="place-1",
        name="Nyra Test",
        city="Москва",
        phones=("+79991234567", "89991234567"),
    )

    assert company.phones == ("+79991234567", "89991234567")


def test_search_criteria_caps_max_results() -> None:
    with pytest.raises(ValidationError):
        SearchCriteria(city="Москва", query="детейлинг", max_results=5001)
