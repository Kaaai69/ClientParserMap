from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import tldextract

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "yclid",
    "ysclid",
    "mc_cid",
    "mc_eid",
}
EXTRACT = tldextract.TLDExtract(suffix_list_urls=())


def normalize_url(value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.casefold() not in {"http", "https"}:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        if not parsed.hostname:
            return None
        host = parsed.hostname.encode("idna").decode("ascii").casefold().rstrip(".")
        port = parsed.port
    except (UnicodeError, ValueError):
        return None

    if (parsed.scheme.casefold(), port) in {("http", 80), ("https", 443)}:
        port = None
    netloc = f"{host}:{port}" if port is not None else host
    path = parsed.path or "/"
    filtered_query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_PARAMETERS
    ]
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            netloc,
            path,
            urlencode(filtered_query, doseq=True),
            "",
        )
    )


def registrable_domain(value: str) -> str | None:
    normalized = normalize_url(value)
    if normalized is None:
        return None
    host = urlsplit(normalized).hostname
    if host is None:
        return None
    extracted = EXTRACT(host)
    if extracted.suffix:
        return f"{extracted.domain}.{extracted.suffix}".casefold()
    return extracted.domain.casefold() or None
