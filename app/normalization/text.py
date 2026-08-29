import re
import unicodedata

NON_WORD_RE = re.compile(r"[^\w]+", flags=re.UNICODE)


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = NON_WORD_RE.sub(" ", normalized)
    return " ".join(normalized.split())


def normalize_name(value: str) -> str:
    return _normalize_text(value)


def normalize_address(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _normalize_text(value)
    return normalized or None
