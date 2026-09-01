"""The niche-to-tag map is what makes OpenStreetMap usable.

Matching a niche as a word in the company name finds a small fraction of a
category: in Moscow, 44 businesses spell out "автосервис" while 888 carry
shop=car_repair. These tests pin the translation and the query it produces.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.domain import SearchCriteria
from app.sources.openstreetmap import (
    OsmCategories,
    _build_query,
    load_categories,
)


def criteria(query: str) -> SearchCriteria:
    return SearchCriteria(city="Москва", query=query, max_results=300)


def test_shipped_categories_load() -> None:
    categories = load_categories()

    assert categories.category
    for item in categories.category:
        assert item.queries and item.selectors


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("автосервис", 'nwr["shop"="car_repair"]'),
        ("Автосервис", 'nwr["shop"="car_repair"]'),
        ("  стоматология  ", 'nwr["amenity"="dentist"]'),
        ("барбершоп", 'nwr["shop"="hairdresser"]'),
        ("шиномонтаж", 'nwr["shop"="tyres"]'),
        ("кафе", 'nwr["amenity"="cafe"]'),
    ],
)
def test_known_niches_query_by_tag(query: str, expected: str) -> None:
    body = _build_query(criteria(query))

    assert expected in body
    assert '["name"]' in body
    assert "~" not in body


def test_unknown_niches_fall_back_to_text_search() -> None:
    body = _build_query(criteria("продажа воздушных шаров"))

    assert "~" in body
    assert "shop" not in body


def test_detailing_still_covers_both_tags() -> None:
    body = _build_query(criteria("детейлинг"))

    assert 'nwr["amenity"="car_wash"]' in body
    assert 'nwr["shop"="car_repair"]' in body


def test_every_preset_niche_that_maps_produces_a_tag_query() -> None:
    from app.presets import NichePresets

    presets = NichePresets.load(Path("app/presets/niche_presets.toml"))
    mapped = [
        query
        for preset in presets.preset
        for query in preset.queries
        if load_categories().selectors_for(query) is not None
    ]

    # The presets exist to be run against OSM; most of them must translate.
    assert len(mapped) >= 25


def test_a_malformed_selector_is_rejected() -> None:
    with pytest.raises(ValidationError):
        OsmCategories.model_validate(
            {"category": [{"queries": ["x"], "selectors": ["not a selector"]}]}
        )
