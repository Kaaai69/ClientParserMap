# Nyraflow Lead Parser — Design Specification

**Date:** 2026-08-12
**Status:** Approved for implementation
**Product:** Production-ready MVP for finding contactable digital-studio leads

## 1. Objective

Build a modular Python service that searches configured business directories for companies in a city and niche, normalizes and deduplicates the results, checks each company's website, extracts public business contacts from the company's own site, calculates transparent opportunity and contactability scores, stores all results in PostgreSQL, and exports qualified contactable leads to Google Sheets.

The MVP must support Google Places, 2GIS Places, and an isolated Yandex Organization Search adapter. It must not use AI/LLMs, visual design analysis, screenshots, Telegram notifications, a CRM, or automated outreach.

The primary success path is:

```text
find a company
  -> identify a website problem or opportunity
  -> find a public contact channel
  -> deliver one deduplicated lead to a manager
```

## 2. Approved constraints and decisions

- Runtime: Python 3.12 or newer.
- Application: FastAPI modular monolith.
- Persistence: PostgreSQL through SQLAlchemy 2.x and Alembic.
- Queue: Redis and a separate RQ worker.
- HTTP: `httpx` with bounded concurrency, retry policies, and timeouts.
- HTML: BeautifulSoup4.
- Export: Google Sheets API using service-account credentials.
- Deployment: Docker and Docker Compose.
- Configuration: environment variables and a separately editable scoring rules file.
- No source API keys or Google Sheets credentials are currently available. Integrations will therefore be verified with contract fixtures; live API calls remain an operator smoke test.
- Companies with a high site opportunity score but no contact are stored with `NO_CONTACTS`, exported to `Все компании`, and excluded from `Готовые лиды`.
- `max_results` is a global cap on unique companies for a search job, not a per-source cap.
- The existing `server.txt` is not an application input and must be excluded from Git and Docker build context.

## 3. Yandex licensing boundary

The current standard Yandex Organization Search API terms prohibit storing or modifying returned organization data. The operator has confirmed that no commercial storage permission is currently available.

`YandexSource` will still implement the common source contract and official API mapping, but it may only be instantiated when both conditions are true:

```text
YANDEX_MAPS_API_KEY is non-empty
YANDEX_STORAGE_ALLOWED=true
```

The default is `YANDEX_STORAGE_ALLOWED=false`. Supplying a key without the explicit storage permission flag leaves the adapter disabled and emits a structured warning. The service never falls back to scraping Yandex Maps HTML. Enabling the flag is an operator assertion that the applicable license permits the intended storage.

References:

- Yandex Organization Search API: https://yandex.com/dev/commercial/doc/ru/concepts/geosearch
- Yandex Organization Search product: https://yandex.com/maps-api/products/geosearch-api

## 4. Architecture

### 4.1 Runtime topology

Docker Compose contains four long-running services and one one-shot migration service:

```text
client/API -> FastAPI -> PostgreSQL
                    \-> Redis/RQ -> worker -> external APIs and websites
                                      \----> PostgreSQL
                                      \----> Google Sheets
```

- `api` validates requests, creates and reads database records, and dispatches durable job-outbox records to RQ. It does not perform directory searches or website crawling.
- `worker` executes one durable search pipeline per RQ job. It uses asynchronous I/O internally for external sources and website analysis.
- `postgres` is the system of record.
- `redis` stores queue and ephemeral RQ state only.
- `migrate` runs `alembic upgrade head` after PostgreSQL becomes healthy; `api` and `worker` start only after successful migration.

### 4.2 Internal modules

```text
app/
  api/                 FastAPI routers, authentication, pagination
  core/                settings, logging, error types, enums
  db/                  SQLAlchemy base, sessions, repositories
  models/              persistent entities
  schemas/             API and source-domain Pydantic schemas
  sources/             LeadSource and the Google/2GIS/Yandex adapters
  normalization/       names, addresses, phones, URLs, contacts
  deduplication/       candidate lookup, matching, deterministic merge
  website_analyzer/    safe fetcher, status, CMS/type detection, contacts
  scoring/             rules loader, opportunity/contact scoring, reasons
  sheets/              idempotent Google Sheets export
  jobs/                transactional outbox, RQ dispatch, resumable pipeline
  main.py              application factory
  cli.py               command-line entry point
  worker.py            RQ worker entry point
```

Modules communicate through typed domain schemas and repository interfaces. Source-specific response shapes do not escape `app/sources`.

## 5. Source contract and adapters

### 5.1 Common contract

Every source implements an asynchronous `LeadSource` protocol with:

- a stable source name;
- an `enabled` state derived from settings;
- paginated search accepting city, query, optional rating/review filters, and a page cursor;
- normalized `SourceCompany` results;
- a `next_cursor` or exhausted marker;
- explicit contact-access status.

`SourceCompany` carries source ID, name, categories, address, all available phones, websites, emails and social links, rating/review count, coordinates, working hours, and source metadata required for provenance.

### 5.2 Google Places

Use Places API (New) Text Search at `places:searchText`. The request uses a production field mask rather than `*` and includes only required place fields: ID, display name, types/primary type, formatted address, national and international phone numbers, website URI, rating, user rating count, location, and regular opening hours.

- Query text combines niche and city.
- Page size is at most 20 and pagination follows `nextPageToken`.
- International phone is primary when present; both national and international values are retained and normalized.
- Optional rating/review filtering is always re-applied locally for consistent behavior across sources.

Reference: https://developers.google.com/maps/documentation/places/web-service/text-search

### 5.3 2GIS Places

Use the official `/3.0/items` Places endpoint with the required basic fields plus point, rubrics, schedule, review statistics, and `contact_groups` when the key permits it.

- Query text combines niche and city.
- Pagination follows the documented page and page-size parameters.
- Missing `contact_groups` never fails a result. The record is marked `contacts_access=LIMITED` and all other fields are preserved.
- Contacts returned through `contact_groups` are classified into phones, websites, email, Telegram, WhatsApp, VK, Instagram, or other social links.

References:

- https://docs.2gis.com/en/api/search/places/overview
- https://docs.2gis.com/en/api/search/places/reference/3.0/items

### 5.4 Yandex Organization Search

Use the official Organization Search endpoint with `type=biz`, the configured API key, the combined niche/city text, language, and documented result limit. Map `CompanyMetaData` fields into `SourceCompany`, including all phones, URLs, categories, hours, rating/review data when returned, and coordinates.

The adapter is excluded from the active source registry unless the licensing gate in section 3 passes.

### 5.5 Source scheduling and rate limits

The worker reads source pages in round-robin order so one directory cannot consume the entire global limit. Each page is locally filtered, normalized, and matched against persisted canonical companies immediately; this provides the unique accepted count used for the stop condition. A final deduplication stage reconciles cross-page candidates. Collection stops when the number of unique accepted companies reaches `max_results` or every source is exhausted.

Each source has configurable concurrency and requests-per-second limits. Requests retry only transient failures:

- `429`, respecting `Retry-After` when supplied;
- `500`, `502`, `503`, and `504`;
- connection and read timeouts.

The default is three attempts with exponential backoff and jitter. Authentication, permission, and validation errors are not retried.

## 6. Search pipeline

### 6.1 Creation

`POST /search` performs these actions:

1. Validate body and optional source selection.
2. Reject the request with a clear conflict response if no requested source is enabled.
3. Insert a `PENDING` search job and one unsent job-outbox row in the same PostgreSQL transaction.
4. After commit, make a best-effort attempt to publish deterministic RQ job `search:<database_job_id>` and mark the outbox row sent.
5. If Redis is temporarily unavailable, keep the job `PENDING`. A FastAPI lifespan dispatcher retries unsent outbox rows using `FOR UPDATE SKIP LOCKED`; the CLI waiting loop invokes the same dispatcher. API startup also recovers rows left unsent by an earlier process crash.

The outbox closes the failure window between committing the database job and publishing it to Redis. The deterministic RQ ID and database job lock make repeated publication harmless. Readiness still reports Redis failure, but an already accepted job is not falsely marked failed merely because the queue is temporarily unavailable.

The CLI calls the same application service rather than duplicating pipeline logic.

### 6.2 Worker stages

The worker takes a database advisory lock for the search job so duplicate RQ deliveries cannot execute it concurrently. It records a stage checkpoint before and after each phase:

```text
COLLECTING
DEDUPLICATING
ANALYZING_WEBSITES
SCORING
EXPORTING
FINISHED
```

The phase flow is:

1. Fetch and locally filter source records.
2. Normalize values and upsert source identities.
3. Deduplicate and merge canonical companies.
4. Analyze websites and collect additional public contacts.
5. Calculate scores, reasons, preferred contact, and lead status.
6. Export qualified contactable leads when Sheets is configured.
7. Finalize counters and status.

Processing is idempotent. Unique constraints protect outbox publication, all source identities, contacts, job/company links, and sheet exports. Per-source cursor/exhaustion state and per-company stage state are stored in PostgreSQL. A retried RQ job resumes from those checkpoints and may safely repeat an upsert.

### 6.3 Status semantics

```text
PENDING                 accepted, not started
RUNNING                 worker is processing the job
COMPLETED               all enabled source/pipeline operations completed
COMPLETED_WITH_ERRORS   useful results exist, but one or more isolated items/sources failed
FAILED                  no source produced usable data or a fatal system error occurred
```

One bad company, website, source, or Sheets row does not abort other work. Errors use stable machine codes and sanitized messages; API keys, credentials, full contacts, and arbitrary response bodies are never logged.

## 7. Persistent data model

Integer identity primary keys are used for database records. Timestamps are timezone-aware UTC values.

### 7.1 `search_jobs`, `search_job_sources`, and `job_outbox`

Stores input parameters, selected sources, status, current stage, timestamps, and counters:

- `found_count`: raw source records seen;
- `filtered_out_count`: records rejected by requested rating/review filters;
- `unique_count`: canonical companies linked to this search;
- `analyzed_count`: companies whose website state was resolved;
- `lead_count`: companies above the site threshold;
- `contactable_lead_count`: above threshold and contactable;
- `exported_count`: rows newly appended or machine fields updated;
- `error_count`: isolated errors.

Each `search_job_sources` row stores one requested source's status, next cursor, exhaustion flag, raw/accepted/error counters, and sanitized last error. Unique `(search_job_id, source)` makes checkpoint updates idempotent.

Each `job_outbox` row uniquely references a search job and stores creation, attempt, publication, and last-error timestamps plus an attempt count. Dispatchers claim unsent rows with `FOR UPDATE SKIP LOCKED`; an RQ job is considered published only after Redis confirms enqueueing.

### 7.2 `companies`

Stores canonical fields and current derived state:

- name, normalized name, primary category, city;
- address and normalized address;
- latitude and longitude;
- canonical website and registrable domain;
- representative rating and review count;
- current website status/type/CMS and CMS confidence;
- `site_opportunity_score`, `contactability_score`, and reasons;
- `contacts_found`, `contact_count`;
- preferred contact type and value;
- lead status (`QUALIFIED`, `BELOW_THRESHOLD`, or `NO_CONTACTS`);
- created/updated/discovered timestamps.

### 7.3 `company_sources`

Stores one source identity per company with unique `(source, source_id)`. It retains the source-provided name, categories, address, coordinates, rating/review pair, working hours, contact-access status, and observation timestamps. Full raw API bodies are not persisted.

### 7.4 `company_contacts` and `company_contact_sources`

`company_contacts` stores type, display value, normalized value, primary flag, and timestamps. Unique `(company_id, type, normalized_value)` prevents duplicates.

`company_contact_sources` stores unique `(contact_id, source)` evidence, where source is `google`, `2gis`, `yandex`, or `website`. This preserves confirmation by multiple sources without duplicating the contact.

Supported types are `PHONE`, `EMAIL`, `WEBSITE`, `TELEGRAM`, `WHATSAPP`, `VK`, `INSTAGRAM`, and `OTHER`.

### 7.5 `website_checks`

Stores analysis history: requested/final URL, status, HTTP status, HTTPS flag, redirect count, response time, content type, CMS/confidence, website type, error code, and check timestamp. Response HTML is not persisted.

### 7.6 Association and export tables

- `search_job_companies` uniquely links a job and company and stores per-job processing state.
- `sheet_exports` uniquely identifies `(spreadsheet_id, worksheet_name, company_id)` and stores row identity plus export timestamps.

Indexes cover normalized phone values, domains, source identities, normalized company/address pairs, score/filter columns, and search-job foreign keys.

## 8. Normalization and deduplication

### 8.1 Normalization

- Trim and collapse whitespace; normalize Unicode and case for comparison while preserving display values.
- Normalize Russian phone numbers to E.164 where valid: leading `8` becomes `+7`; a 10-digit Russian national number receives `+7`. Other countries use `phonenumbers` parsing when a region can be inferred, otherwise retain a conservative normalized `+<digits>` form only when valid.
- Lowercase email domains and remove surrounding punctuation without applying unsafe provider-specific rewrites.
- Accept only HTTP/HTTPS website URLs, infer HTTPS for bare domains, remove fragments and tracking parameters, normalize IDNs, and derive the registrable domain with the public suffix list.
- Normalize social links to stable account/channel identifiers when possible.
- Normalize address abbreviations and punctuation conservatively; do not invent missing locality data.

### 8.2 Deduplication order

Candidate matching is evaluated in this order:

1. Any equal normalized phone.
2. Equal registrable website domain.
3. Equal source ID within the same source.
4. Exact normalized name plus exact normalized address.
5. Name similarity at least `0.86` plus geographic distance no more than `150 m`, only when both records have coordinates.

The first four rules are deterministic matches. The fuzzy/geographic rule uses token similarity and must satisfy both conditions. Records with ambiguous evidence remain separate.

### 8.3 Merge policy

Merging never discards source identities or unique contacts. Canonical text fields prefer non-empty, more informative values; the rating and review count remain a pair taken from the source record with the greatest review count. Source-specific values remain queryable in `company_sources`. Merge operations run in a database transaction and lock candidate company rows.

## 9. Safe website analysis

### 9.1 Fetch policy

Only `http` and `https` are accepted. Before the first request and after every redirect, DNS results are resolved and rejected if any destination is loopback, private, link-local, multicast, reserved, or otherwise non-public. Redirects are bounded, credentials in URLs are rejected, response size is bounded, and only HTML is parsed.

Defaults are configurable:

```text
WEBSITE_TIMEOUT_SECONDS=10
MAX_CONCURRENT_WEBSITE_CHECKS=10
MAX_WEBSITE_REDIRECTS=5
MAX_HTML_BYTES=5000000
MAX_CONTACT_PAGES=8
WEBSITE_CHECK_TTL_HOURS=168
```

The crawler identifies itself with a stable user agent, respects `robots.txt`, performs at most one request at a time per domain, and does not traverse off-domain links.

### 9.2 Website status

Status precedence is deterministic:

- `NO_WEBSITE`: no valid company website exists.
- `TIMEOUT`: DNS/connect/read deadline expired.
- `DEAD`: DNS failure, refused connection, terminal `404/410`, repeated terminal `5xx`, or an invalid/looping redirect chain.
- `ERROR`: an unexpected analysis error or an indeterminate terminal response such as `401`, `403`, or retry-exhausted `429`.
- `PARKED`: reachable HTML matches multiple maintained parking-domain/content fingerprints.
- `PLACEHOLDER`: reachable HTML contains strong under-construction/default-page signals and lacks substantive content.
- `ONLINE`: reachable HTML not classified above.

The check also records HTTP status, final URL, HTTPS use, redirect count, and total response time.

### 9.3 CMS and website type

The detector uses independent weighted fingerprints from headers, meta generator tags, HTML markers, script sources, and stylesheet URLs. It supports:

- Tilda;
- Wix;
- WordPress;
- Webflow;
- Bitrix;
- Nethouse;
- Flexbe;
- Creatium;
- LPmotor;
- `CUSTOM_OR_UNKNOWN`.

Strong vendor-specific markers yield high confidence; multiple weak signals may combine but confidence is capped at `1.0`. WordPress, Bitrix, Webflow, and unknown/custom receive no automatic negative score.

Known hosted card/link domains and strong platform fingerprints, including `*.clients.site`, are classified as `BUSINESS_CARD`; otherwise the type is `NORMAL`. The maintained domain/fingerprint lists are isolated from fetching and scoring logic.

### 9.4 Contact crawl

The analyzer reuses the fetched home page, considers conventional same-origin paths (`/contact`, `/contacts`, `/kontakty`, `/about`), and follows at most three same-origin links whose URL or anchor text clearly indicates contacts. The total is capped at eight pages.

It extracts:

- `tel:` links and phone-like text;
- `mailto:` links and email-like text;
- `t.me` and `telegram.me`;
- `wa.me` and `api.whatsapp.com`;
- `vk.com`;
- public `instagram.com` links;
- the company's own website.

All extracted values pass normalization and validation before upsert with `website` provenance.

## 10. Scoring and qualification

Rules live in `app/scoring/scoring_rules.toml` and are loaded into a validated immutable settings object at startup. Invalid values fail fast with a useful configuration error.

### 10.1 Site opportunity score

Choose the single strongest applicable website signal, then add all reached business-signal thresholds and clamp to `0..100`.

```text
NO_WEBSITE                       100
DEAD or TIMEOUT                   95
PARKED                            95
PLACEHOLDER                       90
BUSINESS_CARD                     80
TILDA, WIX, or NETHOUSE           40
FLEXBE, CREATIUM, or LPMOTOR      30

rating >= 4.7                     +5
reviews_count >= 20               +5
reviews_count >= 50               +5
reviews_count >= 100              +5
```

The strongest-only website rule prevents overlapping type/CMS observations from being double-counted. Reasons list the selected website signal and each business threshold in human-readable Russian.

### 10.2 Contactability score and preferred contact

```text
PHONE                             100
WHATSAPP or TELEGRAM               90
EMAIL                              70
VK, INSTAGRAM, or OTHER            50
no contact                          0
```

The score is the highest available contact-channel value. Preferred contact selection is deterministic: phone, WhatsApp, Telegram, email, VK, Instagram, then other.

### 10.3 Lead state and export eligibility

```text
site score below threshold                         BELOW_THRESHOLD
site score at/above threshold and no contact       NO_CONTACTS
site score at/above threshold and contact exists   QUALIFIED
```

Every processed company is eligible for the `Все компании` Google Sheets worksheet. Only `QUALIFIED` companies are additionally eligible for the `Готовые лиды` worksheet. `LEAD_SCORE_THRESHOLD` defaults to `50` and is environment-configurable.

## 11. Google Sheets export

Sheets is a human-facing work interface, never the primary datastore. If spreadsheet configuration is absent, search and storage still complete; export is skipped with a job warning. The exporter creates and maintains three worksheets.

### 11.1 `Все компании`

This worksheet contains every processed company, including records below the score threshold and companies without contacts. Columns, in order:

```text
Дата обнаружения
Дата обновления
Название
Поисковый запрос
Категория
Город
Адрес
Основной телефон
Дополнительные телефоны
WhatsApp
Telegram
Email
VK
Instagram
Другие соцсети
Сайт
Основной источник
Все источники
Рейтинг
Количество отзывов
Статус сайта
CMS / конструктор
Тип сайта
HTTPS
Site Opportunity Score
Contactability Score
Контакты найдены
Предпочтительный способ связи
Предпочтительный контакт
Причина оценки
Статус лида
ID компании
```

`Основной источник` contains the first discovery source and `Все источники` contains every confirming directory source. `ID компании` is the stable idempotency key and may be hidden, but must not be deleted.

### 11.2 `Готовые лиды`

This worksheet contains only `QUALIFIED` companies. Columns, in order:

```text
Дата добавления
Название
Категория
Город
Адрес
Основной телефон
Дополнительные телефоны
WhatsApp
Telegram
Email
VK
Instagram
Сайт
Источники
Рейтинг
Количество отзывов
Статус сайта
CMS / конструктор
HTTPS
Site Opportunity Score
Contactability Score
Причина попадания
Предпочтительный способ связи
Предпочтительный контакт
Статус работы
Менеджер
Комментарий
ID компании
```

New rows receive `Статус работы=Новый`. The exporter never overwrites the manual columns `Статус работы`, `Менеджер`, and `Комментарий`. Suggested human-managed statuses are `Новый`, `В работе`, `Связались`, `Нет ответа`, `Не интересно`, `Квалифицирован`, and `Закрыто`.

### 11.3 `Запуски поиска`

This worksheet contains one idempotently updated row per search job. Columns, in order:

```text
Дата запуска
Дата завершения
Город
Поисковый запрос
Использованные источники
Минимальный рейтинг
Минимальное количество отзывов
Лимит результатов
Статус запуска
Текущий этап
Всего найдено
Отфильтровано
Уникальных компаний
Проверено сайтов
Потенциальных лидов
Лидов с контактами
Записано в Google Sheets
Количество ошибок
ID запуска
```

`ID запуска` is the stable idempotency key. The row is written when the job starts, refreshed after every stage, and finalized after export.

Export behavior:

1. Create a missing worksheet and exact header row automatically.
2. Load the appropriate `ID компании` or `ID запуска` index.
3. Append a row only when its stable ID is absent.
4. When the row exists, update machine-managed columns only.
5. Never overwrite the three manual workflow columns in `Готовые лиды`.
6. Record every successful append/update in `sheet_exports`, including worksheet and row identity.

Service-account credentials are read from a mounted file. The target spreadsheet must be shared with the service-account email. The implementation uses the official Google client library rather than hand-written JWT signing.

## 12. REST API and CLI

### 12.1 REST API

- `POST /search`: accepts `city`, `query`, nullable `min_rating`, nullable `min_reviews`, `max_results`, and optional source list; returns ID and `PENDING` status.
- `GET /search/{job_id}`: returns status, stage, all counters, and sanitized error summaries.
- `GET /leads`: paginated filters for city, query/category, minimum score, CMS, website status, source, contact availability, and lead state.
- `GET /companies/{id}`: returns canonical company, source observations, contacts with provenance, and latest website check.
- `GET /health/live`: process liveness.
- `GET /health/ready`: verifies PostgreSQL and Redis connectivity.

When `API_AUTH_KEY` is set, every endpoint except health requires `X-API-Key` and compares it in constant time. Authentication is optional for local development but must be configured before exposing the API publicly.

Pagination uses bounded `limit` and `offset`, stable ordering, and total count. Invalid filters return structured `422` responses; unknown IDs return `404`.

### 12.2 CLI

The required command is:

```bash
python -m app.cli search --city "Москва" --query "детейлинг"
```

It accepts the API search parameters plus `--source` and `--no-wait`. By default it enqueues through the same job service, polls PostgreSQL, and prints found, unique, analyzed, site-status, builder/CMS, qualified, contactable, exported, and error counts. It exits nonzero for failed jobs or invalid/no-source configuration.

## 13. Configuration

`.env.example` documents at least:

```text
APP_ENV
API_AUTH_KEY
DATABASE_URL
REDIS_URL
GOOGLE_PLACES_API_KEY
TWO_GIS_API_KEY
YANDEX_MAPS_API_KEY
YANDEX_STORAGE_ALLOWED=false
GOOGLE_SHEETS_SPREADSHEET_ID
GOOGLE_SHEETS_ALL_COMPANIES_WORKSHEET=Все компании
GOOGLE_SHEETS_QUALIFIED_LEADS_WORKSHEET=Готовые лиды
GOOGLE_SHEETS_SEARCH_RUNS_WORKSHEET=Запуски поиска
GOOGLE_SERVICE_ACCOUNT_FILE
LEAD_SCORE_THRESHOLD=50
WEBSITE_TIMEOUT_SECONDS=10
MAX_CONCURRENT_WEBSITE_CHECKS=10
MAX_WEBSITE_REDIRECTS=5
MAX_HTML_BYTES=5000000
MAX_CONTACT_PAGES=8
WEBSITE_CHECK_TTL_HOURS=168
SOURCE_MAX_RETRIES=3
SOURCE_BACKOFF_BASE_SECONDS=1
GOOGLE_REQUESTS_PER_SECOND=5
TWO_GIS_REQUESTS_PER_SECOND=5
YANDEX_REQUESTS_PER_SECOND=1
RQ_JOB_TIMEOUT_SECONDS=7200
LOG_LEVEL
```

The application can start with no external source or Sheets keys. Search creation is rejected until at least one requested source is enabled.

## 14. Observability and operational behavior

Logs are structured JSON and include request ID, search-job ID, stage, source, company ID, duration, result count, retry number, and stable error code where relevant. Raw secrets, authorization headers, full HTML, and contact values are not logged.

Log events cover job lifecycle, source pages, filtering, deduplication/merge decisions, website checks, isolated errors, scoring totals, and Sheets export totals. FastAPI and worker processes handle shutdown signals cleanly; in-flight database transactions roll back and RQ can retry the idempotent job.

PostgreSQL and Redis have Compose health checks. The API readiness endpoint fails if either required dependency is unavailable.

## 15. Testing and verification

No live credentials are available, so the automated test suite must not depend on public APIs.

### 15.1 Unit tests

- URL/domain, phone, email, name, and address normalization.
- Exact and fuzzy/geographic deduplication, non-match boundaries, and lossless merges.
- Every CMS fingerprint and confidence outcome.
- Business-card, parked, and placeholder detection.
- Contact extraction, validation, normalization, provenance merge, priority, and score.
- Every site opportunity rule, business threshold, reason, and score clamp.

### 15.2 Contract tests

Use recorded, redacted JSON fixtures and an HTTP mock transport for Google, 2GIS, and Yandex. Cover field mapping, pagination, missing optional fields, 2GIS limited contacts, `429` plus `Retry-After`, retryable `5xx`, and non-retryable authentication errors.

These tests verify the implementation against documented response contracts, not the current behavior of a live account. README smoke commands explicitly state that live validation requires operator-supplied keys and may incur API charges.

### 15.3 Website analyzer tests

Use a controlled local fixture server and injected DNS/transport policy to cover online pages, redirects, HTTPS metadata, timeout, dead responses, non-HTML, response-size limits, placeholder/parking pages, contact paths, crawl limits, `robots.txt`, and SSRF rejection. Production safety checks remain enabled; tests replace resolution through dependency injection rather than weakening the policy.

### 15.4 Integration tests

Run against PostgreSQL and Redis containers. Cover migrations, atomic job/outbox creation, outbox recovery after simulated Redis failure, duplicate dispatch, RQ execution, per-source cursor resume, database idempotency, deduplication across sources, lead filtering, API authentication, and Sheets append/update behavior through a fake client implementing the exporter interface.

### 15.5 Completion checks

Before delivery, run:

```text
ruff check .
ruff format --check .
mypy app
pytest
docker compose config
docker build .
```

Also start the Compose stack, apply migrations, verify health endpoints, enqueue a fixture-backed search, run a worker through completion, and confirm that rerunning it creates no duplicate companies, contacts, or sheet rows.

## 16. Documentation and acceptance criteria

README documents architecture, environment setup, migrations, Docker and local commands, source enablement, the Yandex licensing gate, Google Sheets service-account sharing, CLI/API examples, scoring rules, CMS fingerprints, troubleshooting, test commands, and live smoke-test limitations.

The MVP is accepted when:

1. `docker compose up -d` starts healthy API, worker, PostgreSQL, and Redis services after migrations.
2. With at least one configured source, the required CLI command creates and completes a durable search job.
3. Source failures and bad websites are isolated and reflected in counters.
4. Companies and contacts from multiple sources deduplicate without losing provenance.
5. Website states, CMS/type, contacts, scores, and Russian reasons are persisted.
6. Every processed company appears idempotently in `Все компании`, each job appears in `Запуски поиска`, and qualified contactable leads additionally appear in `Готовые лиды` while manual workflow columns remain intact.
7. High-opportunity companies without contacts remain queryable as `NO_CONTACTS`, appear in `Все компании`, and do not appear in `Готовые лиды`.
8. Yandex remains disabled without the explicit licensing flag.
9. Automated quality, test, migration, Compose, and image-build checks pass.
10. Live third-party correctness is not claimed until the operator supplies valid keys and runs the documented smoke checks.
