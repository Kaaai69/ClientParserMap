from collections.abc import Mapping
from dataclasses import dataclass

from app.core.enums import CMS


@dataclass(frozen=True, slots=True)
class CmsDetection:
    cms: CMS
    confidence: float
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Fingerprint:
    cms: CMS
    source: str
    marker: str
    weight: float
    label: str


FINGERPRINTS = (
    Fingerprint(CMS.TILDA, "html", "static.tildacdn.com", 0.85, "Tilda CDN"),
    Fingerprint(CMS.TILDA, "html", "t-records", 0.45, "Tilda records"),
    Fingerprint(CMS.TILDA, "html", "tilda-blocks", 0.45, "Tilda blocks"),
    Fingerprint(CMS.WIX, "html", "wix.com website builder", 0.9, "Wix generator"),
    Fingerprint(CMS.WIX, "html", "wixstatic.com", 0.75, "Wix static assets"),
    Fingerprint(CMS.WIX, "html", "parastorage.com", 0.7, "Wix storage"),
    Fingerprint(CMS.WORDPRESS, "html", 'content="wordpress', 0.85, "WordPress generator"),
    Fingerprint(CMS.WORDPRESS, "html", "content='wordpress", 0.85, "WordPress generator"),
    Fingerprint(CMS.WORDPRESS, "html", "/wp-content/", 0.45, "WordPress content"),
    Fingerprint(CMS.WORDPRESS, "html", "/wp-includes/", 0.45, "WordPress includes"),
    Fingerprint(CMS.WORDPRESS, "headers", "wordpress", 0.55, "WordPress header"),
    Fingerprint(CMS.WEBFLOW, "html", "data-wf-page", 0.7, "Webflow page attribute"),
    Fingerprint(CMS.WEBFLOW, "html", "webflow.css", 0.65, "Webflow stylesheet"),
    Fingerprint(CMS.WEBFLOW, "html", 'content="webflow', 0.9, "Webflow generator"),
    Fingerprint(CMS.BITRIX, "html", "/bitrix/", 0.8, "Bitrix assets"),
    Fingerprint(CMS.BITRIX, "headers", "bitrix", 0.85, "Bitrix header"),
    Fingerprint(CMS.NETHOUSE, "html", "nethouse.ru", 0.85, "Nethouse assets"),
    Fingerprint(CMS.NETHOUSE, "url", "nethouse.ru", 0.9, "Nethouse host"),
    Fingerprint(CMS.FLEXBE, "html", "flexbe.com", 0.85, "Flexbe assets"),
    Fingerprint(CMS.FLEXBE, "html", "flexbe.ru", 0.85, "Flexbe assets"),
    Fingerprint(CMS.CREATIUM, "html", "creatium.io", 0.85, "Creatium assets"),
    Fingerprint(CMS.CREATIUM, "html", "creatium.app", 0.85, "Creatium assets"),
    Fingerprint(CMS.LPMOTOR, "html", "lpmotor.ru", 0.85, "LPmotor assets"),
    Fingerprint(CMS.LPMOTOR, "html", "lp-motor", 0.7, "LPmotor marker"),
)


def detect_cms(html: str, headers: Mapping[str, str], url: str) -> CmsDetection:
    sources = {
        "html": html.casefold(),
        "headers": "\n".join(f"{key}: {value}" for key, value in headers.items()).casefold(),
        "url": url.casefold(),
    }
    scores: dict[CMS, float] = {}
    evidence: dict[CMS, list[str]] = {}
    for fingerprint in FINGERPRINTS:
        if fingerprint.marker in sources[fingerprint.source]:
            scores[fingerprint.cms] = scores.get(fingerprint.cms, 0.0) + fingerprint.weight
            evidence.setdefault(fingerprint.cms, []).append(fingerprint.label)

    if not scores:
        return CmsDetection(cms=CMS.CUSTOM_OR_UNKNOWN, confidence=0.0)
    winner = max(scores, key=lambda cms: (scores[cms], cms.value))
    return CmsDetection(
        cms=winner,
        confidence=min(1.0, round(scores[winner], 2)),
        evidence=tuple(dict.fromkeys(evidence[winner])),
    )
