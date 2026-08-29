# OpenStreetMap Overpass Source Design

**Status:** Approved by the user on 2026-08-29.

## Goal

Add OpenStreetMap through the public Overpass API as a keyless lead source. Results must
flow through the existing PostgreSQL, deduplication, website analysis, scoring, Redis/RQ,
and Google Sheets pipeline without changing those downstream contracts.

## Scope

This is a bounded extension of the existing `LeadSource` interface:

- add `SourceName.OPENSTREETMAP` with the serialized value `openstreetmap`;
- add an `OpenStreetMapSource` that performs one bounded Overpass query per search;
- enable the source explicitly with `OPENSTREETMAP_ENABLED=true` (default `false`);
- register it without an API key or billing account;
- deploy it enabled on the current server and run a real smoke search;
- document OpenStreetMap attribution and the public-endpoint limitation.

Google Places, 2GIS, Yandex, the database schema, scoring, exports, and worker topology stay
unchanged. The database columns holding `SourceName` are non-native `VARCHAR` SQLAlchemy
enums without database check constraints, so adding the serialized value needs no migration.

## Query Design

The adapter sends a form-encoded `POST` to
`https://overpass-api.de/api/interpreter` with an Overpass QL program using
`[out:json][timeout:60]`.

The city is resolved inside Overpass with an administrative area selector:

```overpass
area["boundary"="administrative"]["name"="Москва"]->.searchArea;
```

For the approved detailing use case, normalized aliases `детейлинг`, `автодетейлинг`,
`детейлинг авто`, `detailing`, and `car detailing` expand to:

```overpass
nwr["amenity"="car_wash"](area.searchArea);
nwr["shop"="car_repair"](area.searchArea);
```

The mapped aliases intentionally use only these indexed category selectors. A live query
against the shared endpoint returned an HTTP 200 response with an Overpass timeout remark
when city-wide text regex selectors were added; the two indexed selectors return data.

Unknown niches use case-insensitive text selectors over `name`, `brand`, `operator`,
`description`, and `service` with a regex-escaped version of the user's query.
All inserted Overpass string values are JSON-quoted and regex values are escaped before
quoting, so a city or query cannot inject Overpass QL. The result clause is
`out center tags <max_results>;`, making the request bounded by the user's existing
`max_results` validation (1..5000). Overpass has no cursor contract, so the returned
`SourcePage` is exhausted after this response.

## Data Mapping

Each OSM element maps to `SourceCompany` as follows:

- `source_id`: `<element type>/<OSM id>`;
- name: `name`, then `brand`, then `operator`; nameless elements are skipped;
- city: the requested city;
- categories: non-empty `amenity`, `shop`, `craft`, `office`, `tourism`, and `healthcare`
  values formatted as `<key>=<value>`;
- address: `addr:full`, otherwise `addr:street` + `addr:housenumber`;
- phones: `phone`, `contact:phone`, `mobile`, `contact:mobile`;
- emails: `email`, `contact:email`;
- websites: `website`, `contact:website`, `url`;
- Telegram, WhatsApp, VK, Instagram: their direct and `contact:*` tags;
- other social links: Facebook, YouTube, X/Twitter, and Odnoklassniki direct/contact tags;
- coordinates: `lat`/`lon` for nodes, `center.lat`/`center.lon` for ways and relations;
- hours: `{"opening_hours": "<OSM value>"}`;
- rating and review count: absent because OSM does not provide them;
- `contacts_access`: `FULL`, because missing contacts mean absent public tags rather than a
  tariff restriction.

Semicolon-delimited OSM contact values are split, stripped, deduplicated, and kept in
source order. If a website is available, the existing safe website analyzer may find
additional public business contacts.

## Runtime and Reliability

Configuration defaults:

```dotenv
OPENSTREETMAP_ENABLED=false
OVERPASS_API_URL=https://overpass-api.de/api/interpreter
OVERPASS_TIMEOUT_SECONDS=120
OPENSTREETMAP_REQUESTS_PER_SECOND=0.5
OPENSTREETMAP_USER_AGENT=ClientParserMap/1.0
```

The existing `ResilientHttpClient` provides rate limiting and bounded retry behavior for
timeouts, network failures, HTTP 429, and transient 5xx responses. The Overpass query itself
uses a 60-second execution timeout, while the HTTP client allows 120 seconds for delivery.
The server deployment explicitly enables OpenStreetMap.

## Errors and Validation

- a non-list top-level `elements` field is `SOURCE_INVALID_PAYLOAD`;
- malformed individual elements are skipped rather than failing the whole page;
- a non-empty Overpass `remark` is `SOURCE_OVERPASS_REMARK`, retryable, and never an empty
  successful page;
- HTTP and JSON errors continue to use the shared source error codes;
- a non-empty cursor returns an empty exhausted page and never repeats the first request.

## Tests and Acceptance

Contract tests must prove:

1. the request is a form-encoded POST with the configured user agent, bounded result count,
   city area, and indexed detailing tag selectors without city-wide text regex selectors;
2. node and way elements map names, addresses, coordinates, contact fields, categories, and
   opening hours correctly;
3. user-controlled city/query strings in unknown-niche fallback selectors are escaped and
   cannot add Overpass statements;
4. malformed payloads raise `SOURCE_INVALID_PAYLOAD`, malformed elements are skipped, and
   an Overpass remark raises a retryable source error;
5. a cursor does not repeat the network request;
6. configuration and registry enable the keyless source only when explicitly requested.

The full test suite, Ruff checks, formatting check, mypy, Alembic upgrade/check, Docker
Compose configuration, server health checks, and a real `openstreetmap` search must pass.
The real run must reach the current Google Sheets export; the number of rows depends on
current OSM coverage and is not hard-coded.

## Attribution and Operational Limit

README documentation must identify the data as OpenStreetMap contributors under ODbL and
link to `https://www.openstreetmap.org/copyright`. It must also state that the shared public
Overpass endpoint has no commercial SLA and that sustained high-volume collection should
move to a self-hosted Overpass instance or another contractually suitable service.
