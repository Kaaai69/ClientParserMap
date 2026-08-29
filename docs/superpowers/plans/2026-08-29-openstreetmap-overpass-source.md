# OpenStreetMap Overpass Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a keyless, rate-limited OpenStreetMap/Overpass lead source and deploy it into the existing worker and Google Sheets pipeline.

**Architecture:** A focused `OpenStreetMapSource` implements the existing one-page `LeadSource` protocol and maps OSM tags into `SourceCompany`. Configuration and the source registry opt it in without a key; every downstream stage remains unchanged.

**Tech Stack:** Python 3.12, httpx, Pydantic Settings, FastAPI, SQLAlchemy, Redis/RQ, pytest/respx, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-29-openstreetmap-overpass-source-design.md`

## Global Constraints

- The serialized source value is exactly `openstreetmap`.
- OpenStreetMap is disabled by default and enabled only by `OPENSTREETMAP_ENABLED=true`.
- The default endpoint is exactly `https://overpass-api.de/api/interpreter`.
- The default HTTP timeout is 120 seconds and Overpass QL timeout is 60 seconds.
- The default source rate is 0.5 requests per second.
- The default user agent is exactly `ClientParserMap/1.0`.
- No API key, billing account, Nominatim call, database migration, or new Python dependency is introduced.
- Every user-controlled Overpass string is escaped; regex input is regex-escaped before string quoting.
- One request returns at most `SearchCriteria.max_results` and the source does not fake cursor pagination.
- Existing Google, 2GIS, Yandex, PostgreSQL, Redis/RQ, website analysis, scoring, and Google Sheets behavior must remain compatible.
- README attribution links to `https://www.openstreetmap.org/copyright` and describes the public endpoint's lack of commercial SLA.

---

### Task 1: Add, verify, and document the OpenStreetMap Overpass source

**Files:**
- Create: `app/sources/openstreetmap.py`
- Create: `tests/fixtures/overpass_page.json`
- Modify: `app/core/enums.py`
- Modify: `app/core/config.py`
- Modify: `app/sources/registry.py`
- Modify: `tests/contract/test_sources.py`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_source_registry.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `README.md`
- Include: `docs/superpowers/specs/2026-08-29-openstreetmap-overpass-source-design.md`
- Include: `docs/superpowers/plans/2026-08-29-openstreetmap-overpass-source.md`

**Interfaces:**
- Consumes: `LeadSource.search_page(criteria: SearchCriteria, cursor: str | None) -> SourcePage`, `ResilientHttpClient.request_json`, and the existing pipeline's `SourceCompany` contract.
- Produces: `SourceName.OPENSTREETMAP`, `Settings.openstreetmap_enabled`, `Settings.overpass_api_url`, `Settings.overpass_timeout_seconds`, `Settings.openstreetmap_requests_per_second`, `Settings.openstreetmap_user_agent`, and `OpenStreetMapSource` registered by `build_source_registry`.

- [ ] **Step 1: Write the failing contract tests and fixture**

  Add a realistic Overpass fixture containing one named node and one named way with a
  `center`. Between them, include every mapped contact group, `addr:*`, categories, and
  `opening_hours`. Add focused tests equivalent to:

  ```python
  async def test_openstreetmap_builds_bounded_detailing_query_and_maps_contacts(
      respx_mock: MockRouter,
      fixture_json: Callable[[str], dict[str, Any]],
  ) -> None:
      route = respx_mock.post("https://overpass-api.de/api/interpreter").respond(
          json=fixture_json("overpass_page.json")
      )
      source = OpenStreetMapSource(Settings(_env_file=None, openstreetmap_enabled=True))

      page = await source.search_page(criteria(), None)

      request = route.calls[0].request
      body = request.content.decode()
      assert request.headers["User-Agent"] == "ClientParserMap/1.0"
      assert request.headers["Content-Type"].startswith("application/x-www-form-urlencoded")
      assert "amenity" in body and "car_wash" in body
      assert "shop" in body and "car_repair" in body
      assert "300" in body
      assert page.next_cursor is None
      assert page.exhausted is True
      assert page.items[0].source is SourceName.OPENSTREETMAP
      assert page.items[0].source_id == "node/101"
      assert page.items[0].phones == ("+7 999 111-22-33", "+7 999 444-55-66")
      assert page.items[0].websites == ("https://detail.example",)
      assert page.items[1].source_id == "way/202"
      assert page.items[1].latitude == 55.75
      assert page.items[1].longitude == 37.61
  ```

  Add separate tests that (a) submit a city/query containing quotes, brackets, regex
  metacharacters, and an `out;` fragment and prove those fragments occur only in escaped
  quoted values; (b) reject `{"elements": {}}` as `SOURCE_INVALID_PAYLOAD`; and (c) call
  with cursor `"already-read"`, receive an empty exhausted page, and observe zero HTTP
  calls.

- [ ] **Step 2: Run the focused tests and capture RED**

  Run:

  ```bash
  uv run pytest tests/contract/test_sources.py -q
  ```

  Expected: collection fails because `app.sources.openstreetmap` and
  `SourceName.OPENSTREETMAP` do not exist. Record the command and failure in the task report.

- [ ] **Step 3: Implement the source enum and adapter minimally**

  Add:

  ```python
  class SourceName(StrEnum):
      GOOGLE = "google"
      TWO_GIS = "2gis"
      YANDEX = "yandex"
      OPENSTREETMAP = "openstreetmap"
  ```

  Implement `app/sources/openstreetmap.py` with:

  ```python
  OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"
  OVERPASS_QUERY_TIMEOUT_SECONDS = 60
  DETAILING_ALIASES = frozenset(
      {"детейлинг", "автодетейлинг", "детейлинг авто", "detailing", "car detailing"}
  )
  DETAILING_PATTERN = "детейлинг|автодетейлинг|detailing|полировк|керамическ"
  TEXT_TAGS = ("name", "brand", "operator", "description", "service")


  class OpenStreetMapSource:
      name = SourceName.OPENSTREETMAP

      def __init__(self, settings: Settings) -> None:
          self._endpoint = settings.overpass_api_url
          client = httpx.AsyncClient(
              timeout=settings.overpass_timeout_seconds,
              headers={"User-Agent": settings.openstreetmap_user_agent},
          )
          self._http = ResilientHttpClient(
              client,
              max_attempts=settings.source_max_retries,
              backoff_base_seconds=settings.source_backoff_base_seconds,
              requests_per_second=settings.openstreetmap_requests_per_second,
          )

      async def search_page(self, criteria: SearchCriteria, cursor: str | None) -> SourcePage:
          if cursor is not None:
              return SourcePage(items=(), exhausted=True)
          payload = await self._http.request_json(
              "POST",
              self._endpoint,
              data={"data": _build_query(criteria)},
          )
          elements = _payload_elements(payload)
          items = tuple(
              company
              for element in elements
              if (company := _map_company(element, criteria.city)) is not None
          )
          return SourcePage(items=items, exhausted=True)

      async def aclose(self) -> None:
          await self._http.aclose()
  ```

  The constructor creates an `httpx.AsyncClient` with the configured timeout and exact
  `User-Agent`, wrapped in `ResilientHttpClient`. `search_page` posts form data under the
  field `data`. Build the query from small private helpers for QL quoting, regex escaping,
  selector construction, and mapping. Use `json.dumps(value, ensure_ascii=False)` for QL
  strings and `re.escape(value)` before quoting regex input. Implement these exact helper
  interfaces so query construction and payload mapping stay independently testable:

  ```python
  def _build_query(criteria: SearchCriteria) -> str:
      """Return the bounded administrative-area Overpass QL query."""


  def _quoted(value: str) -> str:
      """Return a JSON-compatible quoted Overpass string."""


  def _regex(value: str) -> str:
      """Regex-escape a value and then return its quoted representation."""


  def _payload_elements(payload: dict[str, Any]) -> list[dict[str, Any]]:
      """Validate the top-level elements list and keep dictionary entries."""


  def _map_company(element: dict[str, Any], city: str) -> SourceCompany | None:
      """Map one valid named node, way, or relation; skip malformed elements."""


  def _tag_values(tags: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, ...]:
      """Split semicolon values and return stable, de-duplicated non-empty text."""
  ```

  Map fields exactly as the approved design states. Helper behavior must be typed, reject a
  non-list `elements`, skip malformed individual elements, split semicolon contact values,
  preserve first-seen order, and never construct a coordinate with only one component.

- [ ] **Step 4: Run contract tests and capture GREEN**

  Run:

  ```bash
  uv run pytest tests/contract/test_sources.py -q
  ```

  Expected: all contract tests pass with no warnings.

- [ ] **Step 5: Write failing configuration and registry tests**

  Add behavior tests equivalent to:

  ```python
  def test_openstreetmap_requires_explicit_enablement() -> None:
      assert SourceName.OPENSTREETMAP not in Settings(_env_file=None).enabled_sources
      enabled = Settings(_env_file=None, openstreetmap_enabled=True)
      assert SourceName.OPENSTREETMAP in enabled.enabled_sources


  def test_registry_builds_keyless_openstreetmap_when_enabled() -> None:
      settings = Settings(_env_file=None, openstreetmap_enabled=True)
      assert set(build_source_registry(settings)) == {SourceName.OPENSTREETMAP}
  ```

- [ ] **Step 6: Run configuration tests and capture RED**

  Run:

  ```bash
  uv run pytest tests/unit/test_config.py tests/unit/test_source_registry.py -q
  ```

  Expected: tests fail because the settings fields and registry branch do not yet exist.

- [ ] **Step 7: Wire configuration, registry, Compose, and documentation**

  Add these typed settings and include OpenStreetMap in `enabled_sources` only when the
  boolean is true:

  ```python
  openstreetmap_enabled: bool = False
  overpass_api_url: str = "https://overpass-api.de/api/interpreter"
  overpass_timeout_seconds: float = Field(default=120, gt=0, le=300)
  openstreetmap_requests_per_second: float = Field(default=0.5, gt=0, le=10)
  openstreetmap_user_agent: str = "ClientParserMap/1.0"
  ```

  Register `OpenStreetMapSource(settings)` only under that flag. Add all five environment
  variables to `.env.example` and the shared Docker Compose application environment, using
  the exact defaults in Global Constraints.

  Update README to:

  - list the keyless Overpass source and show `OPENSTREETMAP_ENABLED=true`;
  - show `--source openstreetmap` in CLI usage;
  - attribute `© OpenStreetMap contributors` with
    `https://www.openstreetmap.org/copyright`;
  - state that the shared endpoint has no commercial SLA and high-volume use requires a
    self-hosted or contractually suitable endpoint;
  - include OpenStreetMap in `NO_ENABLED_SOURCES` troubleshooting.

- [ ] **Step 8: Run focused configuration tests and capture GREEN**

  Run:

  ```bash
  uv run pytest tests/unit/test_config.py tests/unit/test_source_registry.py -q
  ```

  Expected: all selected tests pass with no warnings.

- [ ] **Step 9: Run complete local verification**

  Run each command and record its untruncated result:

  ```bash
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy app scripts
  uv run pytest -q
  uv run alembic upgrade head
  uv run alembic check
  docker compose config --quiet
  ```

  Expected: every command exits 0; tests have zero failures and no warnings.

- [ ] **Step 10: Self-review and commit**

  Inspect `git diff --check` and `git diff`. Confirm no secret value, generated credential,
  server password, or unrelated change is present. Commit all task files with:

  ```bash
  git add app tests .env.example docker-compose.yml README.md docs/superpowers
  git commit -m "feat: add OpenStreetMap Overpass source"
  ```

  Write the full RED/GREEN evidence, verification results, file list, and self-review result
  to the assigned SDD report file.
