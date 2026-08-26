from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

from rapidfuzz.fuzz import token_set_ratio

from app.core.enums import SourceName

EARTH_RADIUS_METERS = 6_371_000.0


@dataclass(frozen=True, slots=True)
class CompanyCandidate:
    company_id: int | None
    source: SourceName
    source_id: str
    normalized_name: str
    normalized_address: str | None
    phones: frozenset[str]
    domains: frozenset[str]
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True, slots=True)
class MatchEvidence:
    rule: str
    name_similarity: float | None = None
    distance_meters: float | None = None


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(radians, (lat1, lon1, lat2, lon2))
    delta_lat = lat2_rad - lat1_rad
    delta_lon = lon2_rad - lon1_rad
    value = sin(delta_lat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lon / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * asin(sqrt(value))


def match_company(
    candidate: CompanyCandidate,
    incoming: CompanyCandidate,
) -> MatchEvidence | None:
    if candidate.phones & incoming.phones:
        return MatchEvidence(rule="PHONE")
    if candidate.domains & incoming.domains:
        return MatchEvidence(rule="DOMAIN")
    if candidate.source is incoming.source and candidate.source_id == incoming.source_id:
        return MatchEvidence(rule="SOURCE_ID")
    if (
        candidate.normalized_address
        and incoming.normalized_address
        and candidate.normalized_name == incoming.normalized_name
        and candidate.normalized_address == incoming.normalized_address
    ):
        return MatchEvidence(rule="EXACT_NAME_AND_ADDRESS")
    candidate_latitude = candidate.latitude
    candidate_longitude = candidate.longitude
    incoming_latitude = incoming.latitude
    incoming_longitude = incoming.longitude
    if (
        candidate_latitude is None
        or candidate_longitude is None
        or incoming_latitude is None
        or incoming_longitude is None
    ):
        return None

    similarity = float(token_set_ratio(candidate.normalized_name, incoming.normalized_name))
    distance = haversine_meters(
        candidate_latitude,
        candidate_longitude,
        incoming_latitude,
        incoming_longitude,
    )
    if similarity >= 86 and distance <= 150:
        return MatchEvidence(
            rule="FUZZY_NAME_AND_DISTANCE",
            name_similarity=similarity / 100,
            distance_meters=distance,
        )
    return None
