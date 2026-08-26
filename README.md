# Client Parser Map

Production-ready MVP для поиска потенциальных клиентов: сервис получает компании из официальных API бизнес-каталогов, объединяет дубли, безопасно проверяет сайты, находит публичные контакты, рассчитывает понятный score и синхронизирует результат с Google Sheets.

PostgreSQL — единственный источник истины. Redis/RQ используется для отдельного фонового воркера. Google Sheets — удобное рабочее представление, а не база данных.

## Что умеет система

- Официальный Google Places API (New) Text Search.
- Официальный 2GIS Places API; если тариф не даёт `contact_groups`, компания сохраняется с `contacts_access=LIMITED`.
- Адаптер Yandex Organization Search присутствует, но включается только при одновременных `YANDEX_MAPS_API_KEY` и `YANDEX_STORAGE_ALLOWED=true`.
- Нормализация телефонов, URL, email и публичных соцсетей.
- Консервативное объединение по телефону, домену, ID источника, имени/адресу и имени/координатам.
- SSRF-защита, ручная проверка каждого redirect, лимиты времени, размера HTML и количества страниц.
- Определение Tilda, Wix, WordPress, Webflow, Bitrix, Nethouse, Flexbe, Creatium и LPmotor.
- Правила оценки без AI: файл `app/scoring/scoring_rules.toml` можно менять без изменения кода.
- FastAPI, CLI, PostgreSQL, Redis/RQ worker, Alembic, JSON-логи и Docker Compose.

Система не рассылает сообщения автоматически и не собирает закрытые или персональные данные. Она сохраняет только публичные деловые контакты из разрешённых API и сайтов самих компаний.

## Быстрый запуск через Docker

Требуются Docker Compose, ключ хотя бы одного каталога и, для внешнего доступа, случайный `API_AUTH_KEY`.

```bash
cp .env.example .env
# заполните .env
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

API по умолчанию слушает только `127.0.0.1:8000`. Для удалённой работы безопаснее использовать SSH-туннель или reverse proxy с HTTPS, а не публиковать порт напрямую.

Создание поиска:

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $API_AUTH_KEY" \
  -d '{"city":"Москва","query":"детейлинг","max_results":300}'
```

Проверка запуска:

```bash
curl -H "X-API-Key: $API_AUTH_KEY" http://127.0.0.1:8000/search/1
```

## Конфигурация источников

Минимально задайте один ключ:

```dotenv
GOOGLE_PLACES_API_KEY=
TWO_GIS_API_KEY=
```

API каталогов тарифицируются и ограничивают частоту/объём запросов. Перед рабочим запуском проверьте действующий тариф и квоты в официальной документации [Google Places](https://developers.google.com/maps/documentation/places/web-service/text-search) и [2GIS](https://docs.2gis.com/en/api/search/places/reference/3.0/items).

Яндекс по умолчанию выключен:

```dotenv
YANDEX_MAPS_API_KEY=
YANDEX_STORAGE_ALLOWED=false
```

Не меняйте флаг на `true`, пока ваш коммерческий договор явно не разрешает требуемое хранение данных. Актуальные условия опубликованы в [документации коммерческого API Яндекса](https://yandex.com/dev/commercial/doc/ru/concepts/geosearch).

## Google Sheets

1. Создайте сервисный аккаунт Google Cloud и включите Google Sheets API.
2. Положите JSON-ключ как `credentials/service-account.json`; каталог игнорируется Git, содержимое не попадает в образ.
3. Дайте email сервисного аккаунта доступ редактора к нужной таблице.
4. Добавьте в `.env`:

```dotenv
GOOGLE_SHEETS_SPREADSHEET_ID=идентификатор_из_URL
GOOGLE_SERVICE_ACCOUNT_FILE=/run/secrets/google/service-account.json
```

Без этих параметров поиск, БД и scoring работают, а экспорт пропускается с предупреждением.

Поддерживаются три листа:

- `Все компании` — каждая обработанная компания, включая низкий score и отсутствие контактов.
- `Готовые лиды` — только `QUALIFIED`; поля `Статус работы`, `Менеджер`, `Комментарий` сохраняются при повторной синхронизации.
- `Запуски поиска` — состояние, этапы, счётчики и ошибки каждого запуска.

Стабильные скрываемые ключи — `ID компании` и `ID запуска`. Не удаляйте эти столбцы: по ним работает идемпотентное обновление.

## Основные столбцы лидов

В рабочих листах есть название, запрос, категория, город, адрес, телефоны, WhatsApp, Telegram, email, VK, Instagram, сайт, источники, рейтинг, отзывы, состояние сайта, CMS/конструктор, тип сайта, HTTPS, Site Opportunity Score, Contactability Score, причины, предпочтительный контакт и статус лида. Полный и точный порядок задан в `app/sheets/columns.py`.

## CLI

Локально:

```bash
uv sync --all-groups
uv run alembic upgrade head
uv run python -m app.cli search --city "Москва" --query "детейлинг"
```

Через контейнер:

```bash
docker compose exec api uv run python -m app.cli search \
  --city "Москва" --query "детейлинг" --max-results 300
```

`--source google`, `--source 2gis`, `--min-rating`, `--min-reviews` и `--no-wait` позволяют ограничить запуск.

## Автономный smoke-тест

Smoke-тест не вызывает каталоги, сайты или Google Sheets. Он дважды обрабатывает фиксированные записи Google/2GIS и проверяет объединение в одну компанию:

```bash
docker compose run --rm api uv run python scripts/fixture_smoke.py
docker compose run --rm api uv run python scripts/fixture_smoke.py
```

Во втором JSON-ответе должно быть `"company_count": 1` и `"duplicate_count": 0`.

## Разработка и проверка

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy app scripts
uv run pytest -q
uv run alembic upgrade head
uv run alembic check
```

Тесты используют локальные фикстуры ответов API и не требуют коммерческих ключей.

## Операции

```bash
docker compose logs -f api worker
docker compose restart api worker
docker compose run --rm migrate
```

Не запускайте два проекта с одним `DATABASE_URL` и разными несовместимыми версиями миграций. Перед обновлением делайте резервную копию volume PostgreSQL. JSON-логи автоматически скрывают поля с ключами, токенами, паролями, телефонами, email и контактами.

Частые причины проблем:

- `NO_ENABLED_SOURCES` — нет ключа Google/2GIS либо запрошен запрещённый Яндекс.
- `401` API — отсутствует или неверен `X-API-Key`.
- `health/ready` возвращает `503` — недоступен PostgreSQL или Redis.
- Нет строк в Sheets — неверный spreadsheet ID, файл ключа не смонтирован или таблица не расшарена сервисному аккаунту.
- Контакты 2GIS ограничены — тариф API не даёт поле `contact_groups`; остальные данные всё равно сохраняются.
