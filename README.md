# Client Parser Map

Production-ready MVP для поиска потенциальных клиентов: сервис получает компании из официальных API бизнес-каталогов, объединяет дубли, безопасно проверяет сайты, находит публичные контакты, рассчитывает понятный score и синхронизирует результат с Google Sheets.

PostgreSQL — единственный источник истины. Redis/RQ используется для отдельного фонового воркера. Google Sheets — удобное рабочее представление, а не база данных.

## Что умеет система

- Официальный Google Places API (New) Text Search.
- Официальный 2GIS Places API; если тариф не даёт `contact_groups`, компания сохраняется с `contacts_access=LIMITED`.
- Keyless-источник OpenStreetMap через Overpass API, включаемый явно.
- Адаптер Yandex Organization Search присутствует, но включается только при одновременных `YANDEX_MAPS_API_KEY` и `YANDEX_STORAGE_ALLOWED=true`.
- Нормализация телефонов, URL, email и публичных соцсетей.
- Консервативное объединение по телефону, домену, ID источника, имени/адресу и имени/координатам.
- SSRF-защита, ручная проверка каждого redirect, лимиты времени, размера HTML и количества страниц.
- Определение Tilda, Wix, WordPress, Webflow, Bitrix, Nethouse, Flexbe, Creatium и LPmotor.
- Правила оценки без AI: файл `app/scoring/scoring_rules.toml` можно менять без изменения кода.
- FastAPI, CLI, PostgreSQL, Redis/RQ worker, Alembic, JSON-логи и Docker Compose.

Система не рассылает сообщения автоматически и не собирает закрытые или персональные данные. Она сохраняет только публичные деловые контакты из разрешённых API и сайтов самих компаний.

## Быстрый запуск через Docker

Требуются Docker Compose, включённый источник данных и, для внешнего доступа, случайный `API_AUTH_KEY`.

```bash
cp .env.example .env
# заполните .env
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

API по умолчанию слушает только `127.0.0.1:8000`. Для удалённой работы безопаснее использовать SSH-туннель или reverse proxy с HTTPS, а не публиковать порт напрямую.

## Веб-консоль

На `/` сервис отдаёт рабочую консоль: форма запуска, живой трек этапов и список последних запусков. Кнопка «Запустить поиск» делает ровно то же, что `POST /search`, а по окончании даёт ссылку на Google Таблицу.

Если задан `API_AUTH_KEY`, консоль один раз спросит ключ и сохранит его в `localStorage` браузера — в разметку страницы ключ не попадает и уходит только заголовком `X-API-Key`.

Консоль подтягивает шрифты с Google Fonts. Если они недоступны, страница корректно падает на системные шрифты.

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

## Наборы ниш

Ниша не обязана быть одна. `app/presets/niche_presets.toml` описывает наборы, и кнопка «Запустить поиск» в режиме «Набор ниш» ставит **отдельный запуск на каждую нишу**: одна упавшая ниша не роняет остальные, и в листе «Запуски поиска» видно, что именно дала каждая.

Файл читается на старте, менять его можно без изменения кода:

```toml
[[preset]]
id = "auto"
title = "Авто"
queries = ["детейлинг", "шиномонтаж", "автосервис"]
```

Готовые наборы: `small_business` (15 ниш), `auto`, `beauty`, `services`.

```bash
curl -X POST http://127.0.0.1:8000/search/batch \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $API_AUTH_KEY" \
  -d '{"city":"Москва","preset":"auto","max_results":300}'
```

Вместо `preset` можно передать произвольный список: `{"queries":["кафе","пекарня"]}`. Размер пачки ограничен `MAX_BATCH_SEARCHES` (по умолчанию 50).

Сплошной поиск по городу без ниши не поддерживается: 2ГИС и Google — text-search API и требуют запрос. Для полного охвата используйте набор ниш.

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

OpenStreetMap не требует ключа, но выключен по умолчанию:

```dotenv
OPENSTREETMAP_ENABLED=true
```

Данные © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) и доступны на условиях ODbL. Общий endpoint Overpass не имеет коммерческого SLA; для устойчивого высоконагруженного сбора используйте собственный Overpass или endpoint с подходящим договором.

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
docker compose exec api uv run --no-sync python -m app.cli search \
  --city "Москва" --query "детейлинг" --max-results 300
```

`--source google`, `--source 2gis`, `--source openstreetmap`, `--min-rating`, `--min-reviews` и `--no-wait` позволяют ограничить запуск.

## Эндпоинты

- `GET /` — веб-консоль.
- `GET /meta` — включённые источники, порог score и ссылка на таблицу; консоль рисует себя по этому ответу.
- `POST /search`, `POST /search/batch` (пачка ниш), `GET /search` (список запусков), `GET /search/{id}` (один запуск).
- `GET /leads`, `GET /companies/{id}`.
- `GET /health/live`, `GET /health/ready`.

Всё, кроме `/` и `/health/*`, требует `X-API-Key`, когда задан `API_AUTH_KEY`.

## Автономный smoke-тест

Smoke-тест не вызывает каталоги, сайты или Google Sheets. Он дважды обрабатывает фиксированные записи Google/2GIS и проверяет объединение в одну компанию:

```bash
docker compose run --rm api uv run --no-sync python -m scripts.fixture_smoke
docker compose run --rm api uv run --no-sync python -m scripts.fixture_smoke
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

- `NO_ENABLED_SOURCES` — нет ключа Google/2GIS, не включён `OPENSTREETMAP_ENABLED=true` либо запрошен запрещённый Яндекс.
- `401` API — отсутствует или неверен `X-API-Key`.
- `health/ready` возвращает `503` — недоступен PostgreSQL или Redis.
- Нет строк в Sheets — неверный spreadsheet ID, файл ключа не смонтирован или таблица не расшарена сервисному аккаунту.
- Контакты 2GIS ограничены — тариф API не даёт поле `contact_groups`; остальные данные всё равно сохраняются.
