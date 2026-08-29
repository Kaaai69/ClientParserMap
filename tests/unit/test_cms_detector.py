import pytest

from app.core.enums import CMS
from app.website_analyzer.cms_detector import detect_cms


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('<meta name="generator" content="WordPress 6.5">', CMS.WORDPRESS),
        ('<script src="https://static.tildacdn.com/js/tilda.js"></script>', CMS.TILDA),
        ('<meta name="generator" content="Wix.com Website Builder">', CMS.WIX),
        ('<html data-wf-page="abc"><link href="webflow.css"></html>', CMS.WEBFLOW),
        ('<script src="/bitrix/js/main/core.js"></script>', CMS.BITRIX),
        ('<script src="https://static.nethouse.ru/main.js"></script>', CMS.NETHOUSE),
        ('<script src="https://cdn.flexbe.com/widget.js"></script>', CMS.FLEXBE),
        ('<script src="https://static.creatium.io/app.js"></script>', CMS.CREATIUM),
        ('<script src="https://lpmotor.ru/js/app.js"></script>', CMS.LPMOTOR),
    ],
)
def test_detects_supported_cms(html: str, expected: CMS) -> None:
    result = detect_cms(html, {}, "https://example.ru")

    assert result.cms is expected
    assert result.confidence >= 0.6


def test_multiple_weak_signals_stack_and_confidence_is_capped() -> None:
    html = '<link href="/wp-content/theme.css"><script src="/wp-includes/app.js"></script>'

    result = detect_cms(html, {"X-Powered-By": "WordPress"}, "https://example.ru")

    assert result.cms is CMS.WORDPRESS
    assert result.confidence == 1.0
    assert len(result.evidence) >= 2


def test_unknown_site_has_no_false_positive() -> None:
    result = detect_cms("<html><body>Обычный сайт</body></html>", {}, "https://example.ru")

    assert result.cms is CMS.CUSTOM_OR_UNKNOWN
    assert result.confidence == 0.0
