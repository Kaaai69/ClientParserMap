# Client Parser Map MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and ship a durable lead-search service that collects businesses from configured official directory APIs, enriches and scores them, persists them in PostgreSQL, and maintains three human-friendly Google Sheets worksheets.

**Architecture:** A FastAPI modular monolith writes search jobs and a transactional outbox to PostgreSQL. A Redis/RQ worker runs a resumable asynchronous pipeline through source adapters, normalization/deduplication, safe website analysis, scoring, persistence, and a three-worksheet Google Sheets exporter. PostgreSQL remains the system of record; Sheets is the manager-facing workspace.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2/pydantic-settings, SQLAlchemy 2.x async, Alembic, PostgreSQL, Redis/RQ, httpx, BeautifulSoup4, phonenumbers, tldextract, RapidFuzz, Google Sheets API, Typer, structlog, Docker Compose, pytest, respx, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-12-lead-parser-design.md`

## Global Constraints

- Use Python 3.12 or newer and typed Python throughout `app/`.
- Use only official Google Places, 2GIS Places, and Yandex Organization Search APIs; never scrape directory HTML.
- Keep Yandex disabled unless both `YANDEX_MAPS_API_KEY` is set and `YANDEX_STORAGE_ALLOWED=true`.
- Do not use AI/LLMs, visual design analysis, screenshots, Telegram notifications, CRM integration, or automated outreach.
- Keep secrets exclusively in environment variables or mounted credential files; never commit `.env`, service-account JSON, or `server.txt`.
- Use PostgreSQL as the source of truth and Redis only for RQ/outbox delivery state.
- Export all companies to `Все компании`, qualified contactable leads to `Готовые лиды`, and job state to `Запуски поиска`.
- Never overwrite `Статус работы`, `Менеджер`, or `Комментарий` in an existing `Готовые лиды` row.
- Apply a 10-second default website timeout, bounded redirects/body size/concurrency, and SSRF checks before every request and redirect.
- A single source, company, website, or export-row failure must not abort useful work from other items.
- Follow red-green-refactor for production behavior and run fresh verification before every completion claim.

## File Map

```text
pyproject.toml                         package/dependency/tool configuration
.env.example                          non-secret runtime configuration contract
.gitignore / .dockerignore            secret, cache, worktree, and build exclusions
alembic.ini                           migration runner configuration
alembic/env.py                        async migration environment
alembic/versions/0001_initial.py      complete initial PostgreSQL schema
app/core/config.py                    typed settings and source licensing gate
app/core/enums.py                     shared enum values
app/core/logging.py                   structlog configuration
app/core/errors.py                    stable safe application errors
app/schemas/domain.py                 source/company/contact domain records
app/schemas/api.py                    REST request/response schemas
app/db/base.py                        declarative base and naming convention
app/db/session.py                     async engine/session lifecycle
app/db/models.py                      persistent tables and constraints
app/db/repositories.py                job/company/contact/check/export persistence
app/normalization/*.py                names, addresses, phones, URLs, contacts
app/deduplication/matcher.py           deterministic and fuzzy candidate matching
app/deduplication/service.py           transactional merge policy
app/sources/base.py                    LeadSource and page contract
app/sources/http.py                    retry/backoff/rate-limited request helper
app/sources/google.py                  Google Places (New) adapter
app/sources/two_gis.py                 2GIS Places adapter
app/sources/yandex.py                  license-gated Yandex adapter
app/sources/registry.py                enabled-source construction
app/website_analyzer/security.py       URL/DNS/redirect SSRF policy
app/website_analyzer/checker.py        bounded HTTP website classification
app/website_analyzer/cms_detector.py   CMS weighted fingerprints
app/website_analyzer/website_type.py   card/parking/placeholder fingerprints
app/website_analyzer/contacts.py       shallow contact-page crawl and extraction
app/scoring/scoring_rules.toml         editable rule weights
app/scoring/service.py                 score, reason, and preferred-contact logic
app/sheets/columns.py                  exact worksheet/header contracts
app/sheets/client.py                   Google client protocol and implementation
app/sheets/exporter.py                 idempotent three-worksheet synchronization
app/jobs/outbox.py                     atomic enqueue recovery and RQ publication
app/jobs/pipeline.py                   resumable worker pipeline
app/jobs/tasks.py                      synchronous RQ entry point
app/api/dependencies.py                DB/auth dependencies
app/api/routes/*.py                    search, lead, company, and health endpoints
app/main.py                            FastAPI factory/lifespan dispatcher
app/cli.py                             search CLI
app/worker.py                          RQ worker process
tests/unit/*                           fast deterministic domain tests
tests/contract/*                       official API mapping fixtures/tests
tests/integration/*                    DB, queue, API, and Sheets workflow tests
Dockerfile / docker-compose.yml        production-like local runtime
README.md                              operator and developer runbook
```

---

### Task 1: Project Foundation, Settings, and Domain Contracts

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.dockerignore`
- Create: `.env.example`
- Create: `app/__init__.py`
- Create: `app/core/config.py`
- Create: `app/core/enums.py`
- Create: `app/core/errors.py`
- Create: `app/schemas/domain.py`
- Create: `tests/unit/test_config.py`
- Create: `tests/unit/test_domain.py`

**Interfaces:**
- Consumes: the exact environment-variable names and enum values in the design spec.
- Produces: `Settings`, `get_settings()`, shared enums, `ContactValue`, `SourceCompany`, `NormalizedCompany`, `SourcePage`, and `SearchCriteria`.

- [ ] **Step 1: Add failing settings and domain tests**

```python
def test_yandex_requires_explicit_storage_permission(monkeypatch):
    monkeypatch.setenv("YANDEX_MAPS_API_KEY", "configured")
    monkeypatch.setenv("YANDEX_STORAGE_ALLOWED", "false")
    settings = Settings(_env_file=None)
    assert settings.enabled_sources == ()


def test_search_criteria_rejects_empty_query():
    with pytest.raises(ValidationError):
        SearchCriteria(city="Москва", query=" ", max_results=100)


def test_source_company_keeps_all_contacts():
    company = SourceCompany(
        source=SourceName.GOOGLE,
        source_id="place-1",
        name="Nyra Test",
        city="Москва",
        phones=("+79991234567", "89991234567"),
    )
    assert company.phones == ("+79991234567", "89991234567")
```

- [ ] **Step 2: Run the focused tests and confirm missing-module failures**

Run: `uv run pytest tests/unit/test_config.py tests/unit/test_domain.py -q`

Expected: collection fails because `app.core.config`, enums, and domain schemas do not exist.

- [ ] **Step 3: Create package metadata and typed contracts**

Implement `Settings(BaseSettings)` with `SecretStr | None` keys, database/Redis URLs, sheet names, source rate limits, website bounds, and:

```python
@property
def enabled_sources(self) -> tuple[SourceName, ...]:
    enabled: list[SourceName] = []
    if self.google_places_api_key:
        enabled.append(SourceName.GOOGLE)
    if self.two_gis_api_key:
        enabled.append(SourceName.TWO_GIS)
    if self.yandex_maps_api_key and self.yandex_storage_allowed:
        enabled.append(SourceName.YANDEX)
    return tuple(enabled)
```

Define string enums for source, contact type/access, website status/type, CMS, job status/stage, and lead state. Define immutable Pydantic domain models with tuple-valued contacts and strict search bounds (`1 <= max_results <= 5000`, optional `0..5` rating, nonnegative reviews).

Configure Ruff, mypy, pytest asyncio mode, runtime/dev dependencies, and Python `>=3.12` in `pyproject.toml`. Exclude `.env`, credentials, `server.txt`, `.DS_Store`, `.worktrees/`, caches, and coverage output in ignore files.

- [ ] **Step 4: Run focused tests and static checks**

Run: `uv run pytest tests/unit/test_config.py tests/unit/test_domain.py -q && uv run ruff check app tests/unit/test_config.py tests/unit/test_domain.py && uv run mypy app/core app/schemas`

Expected: all commands exit `0`.

- [ ] **Step 5: Commit the foundation**

```bash
git add pyproject.toml .gitignore .dockerignore .env.example app tests/unit
git commit -m "chore: scaffold typed lead parser"
```

### Task 2: PostgreSQL Schema, Migration, and Repositories

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/0001_initial.py`
- Create: `app/db/__init__.py`
- Create: `app/db/base.py`
- Create: `app/db/session.py`
- Create: `app/db/models.py`
- Create: `app/db/repositories.py`
- Create: `tests/integration/test_repositories.py`

**Interfaces:**
- Consumes: enums and domain objects from Task 1.
- Produces: all design-spec tables, `Database`, `SearchJobRepository`, and `CompanyRepository` with async transaction-safe methods.

- [ ] **Step 1: Add failing repository behavior tests**

```python
@pytest.mark.asyncio
async def test_create_job_writes_outbox_atomically(session):
    repo = SearchJobRepository(session)
    job = await repo.create_with_outbox(SearchCriteria(city="Москва", query="детейлинг"), (SourceName.GOOGLE,))
    await session.commit()
    assert (await session.scalar(select(JobOutbox).where(JobOutbox.search_job_id == job.id))) is not None


@pytest.mark.asyncio
async def test_contact_upsert_merges_provenance(session, company):
    repo = CompanyRepository(session)
    first = await repo.upsert_contact(company.id, ContactType.PHONE, "+79991234567", "+79991234567", SourceName.GOOGLE)
    second = await repo.upsert_contact(company.id, ContactType.PHONE, "8 999 123-45-67", "+79991234567", SourceName.TWO_GIS)
    await session.commit()
    assert first.id == second.id
    assert {item.source for item in second.sources} == {SourceName.GOOGLE, SourceName.TWO_GIS}
```

- [ ] **Step 2: Run the repository tests and confirm schema failures**

Run: `uv run pytest tests/integration/test_repositories.py -q`

Expected: collection fails because DB models/repositories are absent.

- [ ] **Step 3: Implement the complete schema and migration**

Create SQLAlchemy models for `search_jobs`, `search_job_sources`, `job_outbox`, `companies`, `company_sources`, `company_contacts`, `company_contact_sources`, `website_checks`, `search_job_companies`, and `sheet_exports`. Use named constraints, timezone timestamps, JSON only for categories/hours/error summaries, and unique constraints exactly described in the spec. `sheet_exports` uniqueness is `(spreadsheet_id, worksheet_name, entity_type, entity_id)` so both company and job rows are supported.

Implement repositories with these explicit typed methods: `SearchJobRepository.create_with_outbox(criteria: SearchCriteria, sources: tuple[SourceName, ...]) -> SearchJob`, `get(job_id: int, for_update: bool = False) -> SearchJob | None`, `set_stage(job_id: int, stage: JobStage) -> None`, `increment(job_id: int, **counters: int) -> None`, and `finish(job_id: int, status: JobStatus) -> None`; plus `CompanyRepository.create(record: SourceCompany, normalized: NormalizedCompany) -> Company`, `attach_source(company_id: int, record: SourceCompany) -> CompanySource`, `upsert_contact(company_id: int, type: ContactType, value: str, normalized: str, source: SourceName | str, is_primary: bool = False) -> CompanyContact`, and `link_job(job_id: int, company_id: int) -> SearchJobCompany`.

- [ ] **Step 4: Run migration/repository tests**

Run: `uv run alembic upgrade head && uv run pytest tests/integration/test_repositories.py -q`

Expected: migration reaches `0001_initial`; repository tests pass with no duplicate contact rows.

- [ ] **Step 5: Commit persistence**

```bash
git add alembic.ini alembic app/db tests/integration/test_repositories.py
git commit -m "feat: add durable lead persistence"
```

### Task 3: Normalization and Contact Extraction Primitives

**Files:**
- Create: `app/normalization/__init__.py`
- Create: `app/normalization/text.py`
- Create: `app/normalization/phones.py`
- Create: `app/normalization/urls.py`
- Create: `app/normalization/contacts.py`
- Create: `tests/unit/test_normalization.py`
- Create: `tests/unit/test_contact_extraction.py`

**Interfaces:**
- Consumes: `ContactType` and `ContactValue`.
- Produces: `normalize_name`, `normalize_address`, `normalize_phone`, `normalize_url`, `registrable_domain`, `normalize_contact`, and `extract_contacts_from_html`.

- [ ] **Step 1: Add table-driven failing normalization tests**

```python
@pytest.mark.parametrize(("raw", "expected"), [
    ("8 (999) 123-45-67", "+79991234567"),
    ("+7 999 123 45 67", "+79991234567"),
    ("79991234567", "+79991234567"),
])
def test_normalize_russian_phone(raw, expected):
    assert normalize_phone(raw, "RU") == expected


def test_normalize_url_removes_tracking_and_fragment():
    assert normalize_url("Example.RU/path/?utm_source=x#top") == "https://example.ru/path/"


def test_contact_extraction_finds_public_channels():
    html = '<a href="tel:89991234567">call</a><a href="mailto:Sales@Example.ru">mail</a><a href="https://t.me/nyra_test">tg</a>'
    assert {(c.type, c.normalized_value) for c in extract_contacts_from_html(html, "https://example.ru")} == {
        (ContactType.PHONE, "+79991234567"),
        (ContactType.EMAIL, "sales@example.ru"),
        (ContactType.TELEGRAM, "nyra_test"),
    }
```

- [ ] **Step 2: Run and confirm missing-function failures**

Run: `uv run pytest tests/unit/test_normalization.py tests/unit/test_contact_extraction.py -q`

Expected: collection fails on missing normalization modules.

- [ ] **Step 3: Implement conservative normalization/extraction**

Use `unicodedata`, `phonenumbers`, `urllib.parse`, `tldextract.TLDExtract(suffix_list_urls=())`, and BeautifulSoup. Reject invalid schemes/credentials, preserve display values, normalize social handles only for supported public hosts, and deduplicate by `(type, normalized_value)`.

- [ ] **Step 4: Run focused tests and quality checks**

Run: `uv run pytest tests/unit/test_normalization.py tests/unit/test_contact_extraction.py -q && uv run ruff check app/normalization tests/unit/test_normalization.py tests/unit/test_contact_extraction.py && uv run mypy app/normalization`

Expected: all commands exit `0`.

- [ ] **Step 5: Commit normalization**

```bash
git add app/normalization tests/unit/test_normalization.py tests/unit/test_contact_extraction.py
git commit -m "feat: normalize company and contact data"
```

### Task 4: Conservative Company Deduplication and Merge

**Files:**
- Create: `app/deduplication/__init__.py`
- Create: `app/deduplication/matcher.py`
- Create: `app/deduplication/service.py`
- Create: `tests/unit/test_deduplication.py`
- Create: `tests/integration/test_company_merge.py`

**Interfaces:**
- Consumes: normalized domain records and `CompanyRepository`.
- Produces: `MatchEvidence`, `match_company`, `haversine_meters`, and `DeduplicationService.upsert_source_company()`.

- [ ] **Step 1: Add failing match-boundary and lossless-merge tests**

```python
def test_same_phone_is_a_match():
    assert match_company(candidate(phone="+79991234567"), incoming(phone="+79991234567")).rule == "PHONE"


def test_fuzzy_name_without_nearby_coordinates_is_not_a_match():
    assert match_company(candidate(name="Авто Детейлинг", lat=55.75, lon=37.61), incoming(name="Авто Детейлинг", lat=59.93, lon=30.31)) is None


@pytest.mark.asyncio
async def test_merge_keeps_all_sources_and_contacts(session):
    service = DeduplicationService(session)
    company_id = await service.upsert_source_company(google_record)
    assert await service.upsert_source_company(two_gis_same_phone) == company_id
    company = await CompanyRepository(session).get_detail(company_id)
    assert {s.source for s in company.sources} == {SourceName.GOOGLE, SourceName.TWO_GIS}
    assert {c.normalized_value for c in company.contacts} == {"+79991234567", "sales@example.ru"}
```

- [ ] **Step 2: Run and confirm missing-service failures**

Run: `uv run pytest tests/unit/test_deduplication.py tests/integration/test_company_merge.py -q`

Expected: collection fails because matcher/service do not exist.

- [ ] **Step 3: Implement ordered candidate rules and merge policy**

Implement phone, registrable-domain, same-source-ID, exact normalized name/address, then RapidFuzz token similarity `>=86` plus haversine distance `<=150`. Lock database candidates during merges. Preserve all contacts/source observations; choose the non-empty longer text and the rating/review pair with the greatest review count.

- [ ] **Step 4: Run focused and repository regression tests**

Run: `uv run pytest tests/unit/test_deduplication.py tests/integration/test_company_merge.py tests/integration/test_repositories.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit deduplication**

```bash
git add app/deduplication tests/unit/test_deduplication.py tests/integration/test_company_merge.py
git commit -m "feat: deduplicate companies across sources"
```

### Task 5: Resilient Official Directory Source Adapters

**Files:**
- Create: `app/sources/__init__.py`
- Create: `app/sources/base.py`
- Create: `app/sources/http.py`
- Create: `app/sources/google.py`
- Create: `app/sources/two_gis.py`
- Create: `app/sources/yandex.py`
- Create: `app/sources/registry.py`
- Create: `tests/fixtures/google_places_page.json`
- Create: `tests/fixtures/two_gis_page.json`
- Create: `tests/fixtures/yandex_page.json`
- Create: `tests/contract/test_sources.py`
- Create: `tests/unit/test_source_registry.py`

**Interfaces:**
- Consumes: `Settings`, `SearchCriteria`, `SourceCompany`, and `SourcePage`.
- Produces: `LeadSource.search_page(criteria, cursor)`, three adapters, `ResilientHttpClient`, and `build_source_registry(settings)`.

- [ ] **Step 1: Add failing full-fixture contract tests**

```python
@pytest.mark.asyncio
async def test_google_maps_enterprise_fields_and_next_token(respx_mock, fixture_json):
    respx_mock.post("https://places.googleapis.com/v1/places:searchText").respond(json=fixture_json("google_places_page.json"))
    page = await GoogleSource(settings_with_google()).search_page(criteria(), None)
    assert page.next_cursor == "google-next"
    assert page.items[0].phones == ("+7 999 123-45-67", "8 (999) 123-45-67")


@pytest.mark.asyncio
async def test_two_gis_missing_contact_permission_is_limited(respx_mock, fixture_json):
    payload = fixture_json("two_gis_page.json")
    payload["result"]["items"][0].pop("contact_groups")
    respx_mock.get("https://catalog.api.2gis.com/3.0/items").respond(json=payload)
    page = await TwoGisSource(settings_with_two_gis()).search_page(criteria(), None)
    assert page.items[0].contacts_access is ContactsAccess.LIMITED


def test_registry_never_enables_unlicensed_yandex():
    assert SourceName.YANDEX not in build_source_registry(settings_with_yandex(storage_allowed=False))
```

- [ ] **Step 2: Run and confirm missing-adapter failures**

Run: `uv run pytest tests/contract/test_sources.py tests/unit/test_source_registry.py -q`

Expected: collection fails because source modules do not exist.

- [ ] **Step 3: Implement source protocol, retry client, and mappings**

Use documented endpoints and complete redacted fixtures. Google must send an explicit `X-Goog-FieldMask`; 2GIS must request contact groups but tolerate their absence; Yandex must map only official `CompanyMetaData`. Implement retry for connection/timeouts, `429`, and `500/502/503/504`, respecting `Retry-After`, with maximum attempts from settings. Enforce per-source async rate limiting and do not retry `401/403/4xx` validation failures.

- [ ] **Step 4: Add and run transient-failure tests**

Add tests proving two `503` responses followed by a `200` cause exactly three transport requests, `Retry-After` controls the injected sleeper, and `401` causes exactly one request. Run: `uv run pytest tests/contract/test_sources.py tests/unit/test_source_registry.py -q`.

Expected: all tests pass and fixture mappings retain all contacts.

- [ ] **Step 5: Commit source adapters**

```bash
git add app/sources tests/fixtures tests/contract/test_sources.py tests/unit/test_source_registry.py
git commit -m "feat: integrate official company sources"
```

### Task 6: Safe Website Fetching and Status Classification

**Files:**
- Create: `app/website_analyzer/__init__.py`
- Create: `app/website_analyzer/security.py`
- Create: `app/website_analyzer/checker.py`
- Create: `tests/unit/test_website_security.py`
- Create: `tests/unit/test_website_checker.py`

**Interfaces:**
- Consumes: website settings and `WebsiteStatus`.
- Produces: `SafeUrlPolicy.validate(url)`, `WebsiteFetcher.fetch(url)`, and `WebsiteCheckResult`.

- [ ] **Step 1: Add failing SSRF and status tests**

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
async def test_policy_rejects_non_public_dns(ip):
    policy = SafeUrlPolicy(resolver=FakeResolver([ip]))
    with pytest.raises(UnsafeTargetError):
        await policy.validate("https://example.test")


@pytest.mark.asyncio
async def test_checker_revalidates_redirect_target(fake_transport):
    fake_transport.route("https://public.test").redirect("http://127.0.0.1/admin")
    result = await WebsiteFetcher(fake_transport, public_policy()).fetch("https://public.test")
    assert result.status is WebsiteStatus.ERROR
    assert result.error_code == "UNSAFE_REDIRECT"


@pytest.mark.asyncio
async def test_404_is_dead(fake_transport):
    fake_transport.route("https://missing.test").respond(404, "missing")
    assert (await WebsiteFetcher(fake_transport, public_policy()).fetch("https://missing.test")).status is WebsiteStatus.DEAD
```

- [ ] **Step 2: Run and confirm missing-analyzer failures**

Run: `uv run pytest tests/unit/test_website_security.py tests/unit/test_website_checker.py -q`

Expected: collection fails because security/checker modules do not exist.

- [ ] **Step 3: Implement bounded manual redirects and classification**

Resolve every hostname through an injected async resolver and reject non-global IPs with `ipaddress`. Disable httpx automatic redirects, validate each `Location`, cap redirects/body bytes, stream the response, parse only HTML, measure elapsed milliseconds, and classify `NO_WEBSITE`, `TIMEOUT`, `DEAD`, `ERROR`, or provisional `ONLINE` exactly per the spec.

- [ ] **Step 4: Run focused tests and static checks**

Run: `uv run pytest tests/unit/test_website_security.py tests/unit/test_website_checker.py -q && uv run ruff check app/website_analyzer tests/unit/test_website_security.py tests/unit/test_website_checker.py && uv run mypy app/website_analyzer`

Expected: all commands exit `0`.

- [ ] **Step 5: Commit safe fetching**

```bash
git add app/website_analyzer tests/unit/test_website_security.py tests/unit/test_website_checker.py
git commit -m "feat: add safe bounded website checks"
```

### Task 7: CMS, Website Type, and Shallow Contact Crawl

**Files:**
- Create: `app/website_analyzer/cms_detector.py`
- Create: `app/website_analyzer/website_type.py`
- Create: `app/website_analyzer/contacts.py`
- Create: `tests/unit/test_cms_detector.py`
- Create: `tests/unit/test_website_type.py`
- Create: `tests/unit/test_contact_crawler.py`

**Interfaces:**
- Consumes: fetched HTML/final URL and normalization primitives.
- Produces: `detect_cms`, `classify_website`, and `ContactCrawler.crawl(home_result)`.

- [ ] **Step 1: Add failing fingerprint and crawl-bound tests**

```python
@pytest.mark.parametrize(("html", "expected"), [
    ('<meta name="generator" content="WordPress 6.5">', CMS.WORDPRESS),
    ('<script src="https://static.tildacdn.com/js/tilda.js"></script>', CMS.TILDA),
    ('<meta name="generator" content="Wix.com Website Builder">', CMS.WIX),
])
def test_detects_supported_cms(html, expected):
    assert detect_cms(html, {}, "https://example.ru").cms is expected


def test_clients_site_is_business_card():
    assert classify_website("https://shop.clients.site", "<html></html>").website_type is WebsiteType.BUSINESS_CARD


@pytest.mark.asyncio
async def test_crawler_stays_same_origin_and_caps_pages(fake_fetcher):
    contacts = await ContactCrawler(fake_fetcher, max_pages=8).crawl(home_with_ten_contact_links)
    assert fake_fetcher.request_count == 8
    assert all(url.startswith("https://example.ru/") for url in fake_fetcher.requested_urls)
    assert "+79991234567" in {item.normalized_value for item in contacts}
```

- [ ] **Step 2: Run and confirm missing-detector failures**

Run: `uv run pytest tests/unit/test_cms_detector.py tests/unit/test_website_type.py tests/unit/test_contact_crawler.py -q`

Expected: collection fails on missing modules/functions.

- [ ] **Step 3: Implement weighted fingerprints and bounded crawler**

Create explicit fingerprint tables for Tilda, Wix, WordPress, Webflow, Bitrix, Nethouse, Flexbe, Creatium, and LPmotor. Combine independent header/meta/script/link/HTML evidence, cap confidence at `1.0`, and return `CUSTOM_OR_UNKNOWN` without evidence. Detect parked/placeholder/card sites with isolated domain/content rules. Respect parsed `robots.txt`, reuse the home page, try conventional paths, follow no more than three contact-like same-origin links, and cap total pages from settings.

- [ ] **Step 4: Run all website analyzer tests**

Run: `uv run pytest tests/unit/test_website_*.py tests/unit/test_cms_detector.py tests/unit/test_contact_crawler.py -q`

Expected: all tests pass, including SSRF and crawl-bound regressions.

- [ ] **Step 5: Commit enrichment detectors**

```bash
git add app/website_analyzer tests/unit/test_cms_detector.py tests/unit/test_website_type.py tests/unit/test_contact_crawler.py
git commit -m "feat: detect site platforms and contacts"
```

### Task 8: Configurable Scoring and Lead Qualification

**Files:**
- Create: `app/scoring/__init__.py`
- Create: `app/scoring/scoring_rules.toml`
- Create: `app/scoring/service.py`
- Create: `tests/unit/test_scoring.py`

**Interfaces:**
- Consumes: company rating/reviews, website result/CMS/type, and contacts.
- Produces: `ScoringRules.load(path)`, `score_company(input, rules, threshold)`, `ScoringResult`.

- [ ] **Step 1: Add failing literal-expectation scoring tests**

```python
def test_no_website_is_100_and_reason_is_russian(rules):
    result = score_company(score_input(status=WebsiteStatus.NO_WEBSITE, phone="+79991234567"), rules, 50)
    assert result.site_opportunity_score == 100
    assert result.contactability_score == 100
    assert result.lead_state is LeadState.QUALIFIED
    assert "Нет собственного сайта" in result.reasons


def test_tilda_business_signals_stack_but_clamp(rules):
    result = score_company(score_input(cms=CMS.TILDA, rating=4.9, reviews=137, email="sales@example.ru"), rules, 50)
    assert result.site_opportunity_score == 60
    assert result.contactability_score == 70
    assert result.preferred_contact_value == "sales@example.ru"


def test_high_opportunity_without_contact_is_no_contacts(rules):
    assert score_company(score_input(status=WebsiteStatus.DEAD), rules, 50).lead_state is LeadState.NO_CONTACTS
```

- [ ] **Step 2: Run and confirm missing-scoring failures**

Run: `uv run pytest tests/unit/test_scoring.py -q`

Expected: collection fails because scoring modules do not exist.

- [ ] **Step 3: Implement validated TOML rules and deterministic scoring**

Load TOML with `tomllib` into frozen Pydantic models, fail on missing/out-of-range weights, select only the strongest website signal, add rating/review thresholds, clamp to 100, choose the highest contactability channel and specified priority, and generate Russian reasons only for rules that fired.

- [ ] **Step 4: Run scoring plus enum/domain regression tests**

Run: `uv run pytest tests/unit/test_scoring.py tests/unit/test_domain.py -q && uv run mypy app/scoring`

Expected: all commands exit `0`.

- [ ] **Step 5: Commit scoring**

```bash
git add app/scoring tests/unit/test_scoring.py
git commit -m "feat: score and qualify contactable leads"
```

### Task 9: Idempotent Three-Worksheet Google Sheets Workspace

**Files:**
- Create: `app/sheets/__init__.py`
- Create: `app/sheets/columns.py`
- Create: `app/sheets/client.py`
- Create: `app/sheets/exporter.py`
- Create: `tests/unit/test_sheet_exporter.py`

**Interfaces:**
- Consumes: detailed companies/search jobs and worksheet names from settings.
- Produces: `SheetsClient` protocol, `GoogleSheetsClient`, and `SheetsExporter.sync_company()` / `sync_job()`.

- [ ] **Step 1: Add failing behavioral exporter tests with an in-memory client**

```python
@pytest.mark.asyncio
async def test_every_company_is_written_to_all_companies(fake_sheets, below_threshold_company):
    await SheetsExporter(fake_sheets, sheet_settings()).sync_company(below_threshold_company)
    assert fake_sheets.row("Все компании", "ID компании", str(below_threshold_company.id)) is not None
    assert fake_sheets.rows("Готовые лиды") == []


@pytest.mark.asyncio
async def test_qualified_company_is_written_to_both_company_worksheets(fake_sheets, qualified_company):
    await SheetsExporter(fake_sheets, sheet_settings()).sync_company(qualified_company)
    assert len(fake_sheets.rows("Все компании")) == 1
    assert len(fake_sheets.rows("Готовые лиды")) == 1


@pytest.mark.asyncio
async def test_update_preserves_manual_lead_columns(fake_sheets, qualified_company):
    await SheetsExporter(fake_sheets, sheet_settings()).sync_company(qualified_company)
    fake_sheets.edit("Готовые лиды", str(qualified_company.id), {"Статус работы": "В работе", "Менеджер": "Анна", "Комментарий": "Позвонить"})
    await SheetsExporter(fake_sheets, sheet_settings()).sync_company(changed_machine_fields(qualified_company))
    row = fake_sheets.row("Готовые лиды", "ID компании", str(qualified_company.id))
    assert (row["Статус работы"], row["Менеджер"], row["Комментарий"]) == ("В работе", "Анна", "Позвонить")
```

- [ ] **Step 2: Run and confirm missing-exporter failures**

Run: `uv run pytest tests/unit/test_sheet_exporter.py -q`

Expected: collection fails because sheets modules do not exist.

- [ ] **Step 3: Implement exact headers and exporter behavior**

Define the three exact header tuples from spec sections 11.1–11.3. The client protocol exposes `ensure_worksheet`, `read_records`, `append_row`, and `update_row`. Adapt the blocking official Google API client through `asyncio.to_thread`; authenticate with `service_account.Credentials.from_service_account_file`. Index by stable ID, append missing rows, update machine columns, preserve the three manual columns, and sync job rows at stage boundaries.

- [ ] **Step 4: Run exporter and scoring tests**

Run: `uv run pytest tests/unit/test_sheet_exporter.py tests/unit/test_scoring.py -q && uv run mypy app/sheets`

Expected: all commands exit `0` and repeat sync produces one row per stable ID.

- [ ] **Step 5: Commit Sheets workspace**

```bash
git add app/sheets tests/unit/test_sheet_exporter.py
git commit -m "feat: sync three-sheet lead workspace"
```

### Task 10: Transactional Outbox, RQ Dispatch, and Resumable Pipeline

**Files:**
- Create: `app/jobs/__init__.py`
- Create: `app/jobs/outbox.py`
- Create: `app/jobs/pipeline.py`
- Create: `app/jobs/tasks.py`
- Create: `tests/integration/test_outbox.py`
- Create: `tests/integration/test_pipeline.py`

**Interfaces:**
- Consumes: repositories, source registry, deduplication, analyzer, scoring, Sheets exporter, Redis settings.
- Produces: `OutboxDispatcher.dispatch_pending()`, `SearchPipeline.run(job_id)`, and RQ function `run_search_job(job_id)`.

- [ ] **Step 1: Add failing durability and end-to-end fixture tests**

```python
@pytest.mark.asyncio
async def test_dispatcher_retries_unsent_row_after_redis_recovers(session, flaky_queue):
    job = await SearchJobRepository(session).create_with_outbox(criteria(), (SourceName.GOOGLE,))
    await session.commit()
    assert await OutboxDispatcher(session_factory, flaky_queue).dispatch_pending() == 0
    flaky_queue.recover()
    assert await OutboxDispatcher(session_factory, flaky_queue).dispatch_pending() == 1
    assert flaky_queue.job_ids == [f"search:{job.id}"]


@pytest.mark.asyncio
async def test_pipeline_deduplicates_analyzes_scores_and_exports_once(pipeline_fixture):
    result = await pipeline_fixture.run_twice()
    assert result.job.status is JobStatus.COMPLETED
    assert result.job.found_count == 2
    assert result.job.unique_count == 1
    assert result.company.lead_state is LeadState.QUALIFIED
    assert len(result.sheets.rows("Все компании")) == 1
    assert len(result.sheets.rows("Готовые лиды")) == 1
    assert len(result.sheets.rows("Запуски поиска")) == 1
```

- [ ] **Step 2: Run and confirm missing-job-module failures**

Run: `uv run pytest tests/integration/test_outbox.py tests/integration/test_pipeline.py -q`

Expected: collection fails because outbox/pipeline modules do not exist.

- [ ] **Step 3: Implement deterministic dispatch and staged pipeline**

Claim outbox rows with PostgreSQL `FOR UPDATE SKIP LOCKED`, enqueue deterministic IDs, and mark only confirmed publications sent. Run source pages round-robin, persist cursor/exhaustion after each page, filter locally, deduplicate immediately, link job/company rows, analyze with `asyncio.Semaphore`, save website checks/contacts, score, sync Sheets, and finalize `COMPLETED`, `COMPLETED_WITH_ERRORS`, or `FAILED`. Catch per-item/source failures into sanitized counters. The synchronous RQ entry point uses `asyncio.run` and a fresh engine lifecycle.

- [ ] **Step 4: Run durability, integration, and retry tests**

Run: `uv run pytest tests/integration/test_outbox.py tests/integration/test_pipeline.py tests/contract/test_sources.py -q`

Expected: all tests pass; the second pipeline run creates no duplicates.

- [ ] **Step 5: Commit job processing**

```bash
git add app/jobs tests/integration/test_outbox.py tests/integration/test_pipeline.py
git commit -m "feat: process durable search jobs"
```

### Task 11: FastAPI, Authentication, Health, and CLI

**Files:**
- Create: `app/api/__init__.py`
- Create: `app/api/dependencies.py`
- Create: `app/api/routes/__init__.py`
- Create: `app/api/routes/search.py`
- Create: `app/api/routes/leads.py`
- Create: `app/api/routes/companies.py`
- Create: `app/api/routes/health.py`
- Create: `app/schemas/api.py`
- Create: `app/main.py`
- Create: `app/cli.py`
- Create: `app/worker.py`
- Create: `tests/integration/test_api.py`
- Create: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: settings, repositories, outbox dispatcher, Redis queue, job and company models.
- Produces: `create_app()`, required REST routes, `python -m app.cli search`, and `python -m app.worker`.

- [ ] **Step 1: Add failing API/CLI behavior tests**

```python
@pytest.mark.asyncio
async def test_post_search_creates_pending_job(async_client, configured_google):
    response = await async_client.post("/search", json={"city": "Москва", "query": "детейлинг", "max_results": 300})
    assert response.status_code == 202
    assert response.json()["status"] == "PENDING"


@pytest.mark.asyncio
async def test_search_rejected_when_no_source_enabled(async_client):
    response = await async_client.post("/search", json={"city": "Москва", "query": "детейлинг"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "NO_ENABLED_SOURCES"


def test_cli_prints_completed_counts(cli_runner, completed_job_service):
    result = cli_runner.invoke(app, ["search", "--city", "Москва", "--query", "детейлинг"])
    assert result.exit_code == 0
    assert "Unique: 1" in result.stdout
    assert "Exported to Google Sheets: 1" in result.stdout
```

- [ ] **Step 2: Run and confirm missing-interface failures**

Run: `uv run pytest tests/integration/test_api.py tests/unit/test_cli.py -q`

Expected: collection fails because API/main/CLI modules do not exist.

- [ ] **Step 3: Implement routes, lifespan, auth, and commands**

Create `POST /search` (`202`), `GET /search/{id}`, filtered/paginated `GET /leads`, detailed `GET /companies/{id}`, `/health/live`, and dependency-checking `/health/ready`. Use constant-time `X-API-Key` validation only when configured. Start a cancellable outbox dispatch loop during FastAPI lifespan. Implement Typer `search` with source/filter/limit/`--no-wait` options and database polling; implement an RQ worker bound to the configured queue.

- [ ] **Step 4: Run API, CLI, and pipeline integration tests**

Run: `uv run pytest tests/integration/test_api.py tests/unit/test_cli.py tests/integration/test_pipeline.py -q && uv run mypy app/api app/main.py app/cli.py app/worker.py`

Expected: all commands exit `0`.

- [ ] **Step 5: Commit public interfaces**

```bash
git add app/api app/schemas/api.py app/main.py app/cli.py app/worker.py tests/integration/test_api.py tests/unit/test_cli.py
git commit -m "feat: expose search API and CLI"
```

### Task 12: Structured Logging, Containers, CI, and Operator Documentation

**Files:**
- Create: `app/core/logging.py`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `scripts/fixture_smoke.py`
- Create: `.github/workflows/ci.yml`
- Create: `README.md`
- Create: `tests/unit/test_logging.py`
- Create: `tests/integration/test_fixture_smoke.py`
- Modify: `app/main.py`
- Modify: `app/worker.py`
- Modify: `app/jobs/pipeline.py`

**Interfaces:**
- Consumes: all completed application entry points and settings.
- Produces: JSON logging, healthy Compose deployment, credential-free fixture smoke command, CI, and complete runbook.

- [ ] **Step 1: Add failing sanitized-log and smoke tests**

```python
def test_structured_log_redacts_secrets_and_contacts(capsys):
    configure_logging("INFO")
    get_logger().info("source_failed", api_key="secret-key", phone="+79991234567", error_code="SOURCE_TIMEOUT")
    output = capsys.readouterr().out
    assert "secret-key" not in output
    assert "+79991234567" not in output
    assert '"error_code":"SOURCE_TIMEOUT"' in output.replace(" ", "")


def test_fixture_smoke_is_idempotent(compose_services):
    first = run_fixture_smoke()
    second = run_fixture_smoke()
    assert first.unique_count == second.unique_count == 1
    assert second.duplicate_count == 0
```

- [ ] **Step 2: Run and confirm missing-operational-module failures**

Run: `uv run pytest tests/unit/test_logging.py tests/integration/test_fixture_smoke.py -q`

Expected: tests fail because logging and fixture smoke entry point are absent.

- [ ] **Step 3: Implement operational assets and runbook**

Configure structlog JSON with a processor that drops/redacts keys matching secret/token/key/password/phone/email/contact. Build a non-root Python 3.12 image. Compose services are `postgres`, `redis`, `migrate`, `api`, and `worker` with health checks, persistent Postgres volume, mounted service-account file, and migration dependency. CI runs Ruff, mypy, pytest with Postgres/Redis services, Alembic, and Docker build.

Document exact local/Docker setup, environment variables, migrations, CLI/API examples, three worksheet schemas, Sheets service-account sharing, scoring edits, CMS fingerprints, Yandex licensing gate, source billing/rate-limit caveats, credential-free fixture smoke, live smoke commands, and troubleshooting.

- [ ] **Step 4: Run the complete local verification matrix**

Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -q
uv run alembic upgrade head
docker compose config
docker build -t client-parser-map:test .
```

Expected: every command exits `0`.

- [ ] **Step 5: Run Compose fixture smoke and idempotency check**

Run:

```bash
docker compose up -d --build
docker compose run --rm api python scripts/fixture_smoke.py
docker compose run --rm api python scripts/fixture_smoke.py
curl --fail http://localhost:8000/health/live
curl --fail http://localhost:8000/health/ready
docker compose ps
```

Expected: both smoke runs complete; the second reports zero duplicates; both health calls return `200`; required services are healthy.

- [ ] **Step 6: Commit operations and documentation**

```bash
git add app/core/logging.py app/main.py app/worker.py app/jobs/pipeline.py Dockerfile docker-compose.yml scripts .github README.md tests/unit/test_logging.py tests/integration/test_fixture_smoke.py
git commit -m "docs: package and document client parser"
```

### Task 13: Requirement Audit and GitHub Delivery

**Files:**
- Modify: any file with a verified requirement gap
- Test: the full test suite and Docker/Compose checks

**Interfaces:**
- Consumes: the design spec and Tasks 1–12 deliverables.
- Produces: verified Git history pushed to `Kaaai69/ClientParserMap` without secrets.

- [ ] **Step 1: Audit every acceptance criterion against code and tests**

Create an internal checklist mapping design-spec acceptance criteria 1–10 to a command/test and inspect `git diff --check`, `git status --short`, tracked files, and ignored secret paths. Any discovered behavioral gap first receives a failing regression test, then the minimal fix.

- [ ] **Step 2: Run fresh final verification**

Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -q
docker compose config
docker build -t client-parser-map:final .
git diff --check
git status --short
```

Expected: quality/test/build commands exit `0`; status contains no credentials, `.env`, service-account JSON, `server.txt`, or unintended files.

- [ ] **Step 3: Connect and push the approved private repository**

```bash
git remote add origin https://github.com/Kaaai69/ClientParserMap.git
git push -u origin HEAD:main
```

If `origin` already exists, verify it is exactly `https://github.com/Kaaai69/ClientParserMap.git` before pushing. Do not force-push.

- [ ] **Step 4: Verify remote state**

Run: `gh repo view Kaaai69/ClientParserMap --json url,visibility,defaultBranchRef && gh api repos/Kaaai69/ClientParserMap/commits/main --jq '.sha'`

Expected: repository remains private, default branch is `main`, and remote SHA equals local `git rev-parse HEAD`.

### Task 14: Secure Server Deployment

**Files:**
- Create on server: `/opt/client-parser-map/.env`
- Create on server: `/opt/client-parser-map/compose.production.yml`
- Create on server: `/opt/client-parser-map/credentials/` only when Sheets credentials are later supplied
- Test: remote Docker Compose state and HTTP health endpoints

**Interfaces:**
- Consumes: verified `main` from `Kaaai69/ClientParserMap`, the operator-provided VPN/SSH access, and server-only secrets.
- Produces: a restart-safe remote deployment with persistent PostgreSQL/Redis state and a loopback-only API until TLS ingress is configured.

- [ ] **Step 1: Inspect the target host read-only**

Connect using the existing operator credentials without printing them. Record OS, free disk/memory, Docker/Compose versions, active listeners, firewall, and existing reverse-proxy/container names. Do not install packages or stop services during inspection.

- [ ] **Step 2: Prepare isolated server paths and secrets**

Create `/opt/client-parser-map`, clone the private repository through the already authorized mechanism, and create a mode-`0600` `.env` containing generated database credentials and `API_AUTH_KEY`. Keep source API keys and Sheets credentials empty until supplied. Never copy `vpn.conf`, `server.txt`, GitHub tokens, or private SSH material into the repository or image.

- [ ] **Step 3: Start production Compose without exposing an unauthenticated public port**

Use a production override with restart policies, named volumes, resource-aware worker settings, and API publication `127.0.0.1:8000:8000`. Run migrations first, then start API and worker. Do not change an existing reverse proxy or firewall unless its exact ownership and collision-free route are verified.

- [ ] **Step 4: Verify persistence, health, and restart behavior**

Run remote checks equivalent to:

```bash
docker compose -f docker-compose.yml -f compose.production.yml ps
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
docker compose -f docker-compose.yml -f compose.production.yml restart api worker
curl --retry 20 --retry-delay 2 --fail http://127.0.0.1:8000/health/ready
```

Expected: PostgreSQL, Redis, API, and worker return to healthy/running state; migrations are at head; volumes remain attached; logs contain no credentials.

- [ ] **Step 5: Deliver the access and activation runbook**

Document the SSH-tunnel command for local API access, remote Compose update/rollback commands, backup path/command, and exact fields the operator must later add for Google/2GIS and Google Sheets. State explicitly that live searching/export remains disabled until those third-party credentials are supplied.
