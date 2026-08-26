from app.core.enums import SourceName
from app.deduplication.matcher import CompanyCandidate, haversine_meters, match_company


def candidate(**overrides: object) -> CompanyCandidate:
    values: dict[str, object] = {
        "company_id": 1,
        "source": SourceName.GOOGLE,
        "source_id": "g-1",
        "normalized_name": "авто детейлинг",
        "normalized_address": "москва тверская 10",
        "phones": frozenset(),
        "domains": frozenset(),
        "latitude": None,
        "longitude": None,
    }
    values.update(overrides)
    return CompanyCandidate(**values)  # type: ignore[arg-type]


def incoming(**overrides: object) -> CompanyCandidate:
    values = {
        "company_id": None,
        "source": SourceName.TWO_GIS,
        "source_id": "2g-1",
    }
    values.update(overrides)
    return candidate(**values)


def test_same_phone_is_a_match() -> None:
    evidence = match_company(
        candidate(phones=frozenset({"+79991234567"})),
        incoming(phones=frozenset({"+79991234567"})),
    )

    assert evidence is not None
    assert evidence.rule == "PHONE"


def test_same_domain_is_a_match() -> None:
    evidence = match_company(
        candidate(domains=frozenset({"example.ru"})),
        incoming(domains=frozenset({"example.ru"})),
    )

    assert evidence is not None
    assert evidence.rule == "DOMAIN"


def test_fuzzy_name_without_nearby_coordinates_is_not_a_match() -> None:
    evidence = match_company(
        candidate(normalized_name="авто детейлинг", latitude=55.75, longitude=37.61),
        incoming(normalized_name="авто детейлинг москва", latitude=59.93, longitude=30.31),
    )

    assert evidence is None


def test_fuzzy_name_and_nearby_coordinates_match() -> None:
    evidence = match_company(
        candidate(normalized_name="авто детейлинг", latitude=55.7500, longitude=37.6100),
        incoming(normalized_name="авто детейлинг москва", latitude=55.7504, longitude=37.6104),
    )

    assert evidence is not None
    assert evidence.rule == "FUZZY_NAME_AND_DISTANCE"
    assert evidence.distance_meters is not None and evidence.distance_meters < 150


def test_haversine_reports_realistic_distance() -> None:
    distance = haversine_meters(55.7500, 37.6100, 55.7510, 37.6100)

    assert 110 < distance < 112
