# Jana Address

**High-performance автономный сервис структурного разбора адресов Казахстана: REST API + веб-интерфейс.**

Сервис принимает свободно написанный адрес (`"г. Актобе, ул. Абая, д. 55/6, кв 12"`),
выделяет 14 полей по ТЗ и возвращает их в формате JSON. Текстовые поля — **точные
слайсы исходной строки** (опечатки, регистр и падежи не исправляются), а
классификаторы (`province`, `city`, `district`, `street_type`) приводятся к
каноническим значениям из базы адресов.

`Python 3.12` · `FastAPI` · `SQLite FTS5` · `React 19 + Vite` · `nginx` · `Docker` · `ruff` · `pytest`

| Компонент | Адрес |
|---|---|
| Веб-интерфейс | **http://localhost:8080** |
| REST API / Swagger | **http://localhost:8000** · `/docs` |
| Healthcheck | http://localhost:8000/api/v1/health |

---

## Ключевые показатели

| Показатель | Значение |
|---|---|
| 1 000 адресов, холодный LRU-кэш | **~1.35 с** (медиана 3 прогонов), лучший ~1.31 с |
| На одну запись | **~1.31–1.36 мс** |
| Лимит ТЗ | 300 с на 1 000 записей |
| Запас по скорости | **~220×** |
| База адресов | 49 541 улица/жилой массив, 20 областей, 238 городов, 230 районов |
| Режим БД | SQLite + FTS5, read-only (`mode=ro`), hot-reload по mtime |
| Размер боевой БД | 246.6 MB (вшита в Docker-образ API) |
| QA | `ruff check` — 0 ошибок; `pytest` — 86 passed |

---

## Архитектура и инженерия

```
raw_address
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. WorkText: offset-safe предобработка                       │
│    "66квартал" → "66 квартал", "flat191" → "flat 191"        │
│    вставляются только пробелы — смещения исходной строки     │
│    не меняются, поэтому все ответы остаются слайсами         │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. Tail Parser (regex-грамматики)                            │
│    house / apartment / entrance / floor / landmark           │
│    найденные спаны маскируются, чтобы не попасть в FTS       │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. Гибридный поиск в SQLite FTS5                             │
│    строгий AND → ослабленный AND → OR + bm25                 │
│    двухуровневая схема v2:                                   │
│      name_norm       — полное имя с типом                    │
│      name_core_norm  — ядро без типа ("нурсултана назарбаева")│
│      aliases         — старые названия                       │
│    скользящее окно по названию + difflib-похожесть           │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. Защита от гео-галлюцинаций                                │
│    score ≥ 0.8; проверка неоднозначности по городам;         │
│    запрет обогащения чужими родителями; fallback → 0.5       │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
14 полей JSON + confidence + X-Records / X-Elapsed-Ms
```

### Гибридный алгоритм

1. **Tail Parser** вырезает структурные элементы по регулярным грамматикам:
   дом с дробями и литерами (`55/6`, `12а`), квартиру (`кв`, `flat`, `kv`),
   подъезд (`под`, `подъезд`), этаж (`эт`, `этаж`) и ориентиры
   (`магазин Береке`, `Qazpost`). Поддерживаются слитные написания:
   `12дом`, `4подъезд`, `flat191`, `г.Алматы`, `Актобе/Ақтөбе`.
2. **FTS5 lookup** ищет остаток строки в `fts_address`. Запрос постепенно
   ослабляется: строгий `AND` → укороченный `AND` → `OR` с ранжированием
   `bm25`. Поэтому лишние слова (казахская часть двуязычной склейки, ориентир)
   не блокируют точное совпадение.
3. **Двухуровневая схема v2.** Помимо полного `name_norm` база хранит
   `name_core_norm` — название без базового слова типа. Пользователь обычно
   пишет «Абая», «Мангилик Ел», «Аксай 3а» без «улица/проспект/микрорайон»,
   поэтому ядро обязательно участвует в сопоставлении. Дополнительно
   сравниваются `normalize(name_official)` и старые названия из `aliases`.
4. **Локализация названия.** Скользящее окно по токенам адреса находит точный
   спан названия: в ответ уходит `raw_address[start:end]`, а не значение из БД.
   Поэтому `"казибек би"` остаётся `"казибек би"`, даже если в базе
   `"Казыбек би"`.
5. **Родительские сущности** (`city`, `district`, `province`) подтягиваются из
   БД только при доверенном совпадении. `province` также выводится из
   найденного города через `cities.province_id`.

### Защита от гео-галлюцинаций

- Сущность принимается только при похожести **≥ 0.8**; иначе — regex-fallback
  без обогащения регионами.
- Если одинаковое название встречается в нескольких городах, а город в адресе
  не указан, сервис **не выбирает случайный город**: гео-поля остаются пустыми,
  `confidence` — `0.5…0.6`.
- Если город явно указан пользователем, родительские сущности из «чужого»
  города не подмешиваются.
- Псевдорайоны (`X Г.А.`) и районы, названные как города (`Алматы`, `Тараз`),
  не дублируют `city`/`district`.
- Если слово одновременно является районом и улицей/проспектом в БД
  (`Туран`), без явного «район / аудан / р-н» приоритет отдаётся улице.
  Явное указание района сохраняет `district`.
- Если одноимённая улица есть в нескольких городах, а город в строке не
  указан (`Туран`, `Мангилик Ел`), `province`/`city`/`district` остаются
  `null`, `confidence` — `0.5`. Это важнее «красивого» заполнения: угаданный
  город хуже, чем честный `null`.
- Неизвестные остатки строки не превращаются в `landmark`: ориентир
  заполняется только по известным грамматикам (`магазин ...`, `Qazpost` и т.п.).
- При отсутствии файла БД `NullAddressRepository` включает полностью рабочий
  regex-разбор с `confidence = 0.5`; появившаяся база подхватывается без
  рестарта (`DB_RELOAD_INTERVAL`).

### Нормализация казахских символов

Поиск сводит казахские буквы к русским аналогам (`ә→а`, `қ→к`, `ң→н`,
`ұ→у`, `ү→у`, `ө→о`, `һ→х`, `і→и`), поэтому `"Мәңгілік Ел"` и
`"Мангилик ел"` находят одну и ту же сущность. Ответ при этом сохраняет
исходное написание пользователя.

### Производительность и кэширование

- LRU-кэш разобранных адресов — до **200 000** записей на процесс.
- Дополнительные LRU-кэши: похожесть (65 536), сравнение токенов (65 536),
  варианты названий (32 768), ключи классификаторов (16 384).
- Пакет обрабатывается в `ThreadPoolExecutor` (4 потока по умолчанию);
  CPU-bound разбор уводится из event loop через `run_in_threadpool`.
- SQLite-соединения thread-local, только чтение (`mode=ro`,
  `PRAGMA query_only = 1`): API не изменяет вшитую в образ БД.
- Быстрый путь: точное совпадение названия не перебирает окна и не вызывает
  `difflib`.

---

## Быстрый запуск

Готовые образы лежат в GitHub Container Registry, и **вся база адресов уже
внутри образа API** (а офлайн-карта — внутри образа web). Скачивания БД,
volume и seed-файлов при запуске больше нет.

Требуется Docker с Compose v2:

```bash
docker compose up -d
```

Compose скачает:

| Сервис | Образ | Порт | Что внутри |
|---|---|---|---|
| `api` | `ghcr.io/hydr0x1de/posttech-hackathon-api:1.0.0` | `8000` | FastAPI + SQLite FTS5 + `address_base.db` |
| `web` | `ghcr.io/hydr0x1de/posttech-hackathon-web:1.0.0` | `8080` | React/Vite UI + nginx + PMTiles |

- **Интерфейс:** http://localhost:8080
- **API:** http://localhost:8000
- **Swagger:** http://localhost:8000/docs

После первого `docker compose pull` образы лежат локально, и запуск работает
без сети. `web` стартует после того, как healthcheck API подтвердит
`db_loaded: true`.

### Пересборка образов (для мейнтейнеров)

Для локальной сборки нужен файл `backend/data/address_base.db` (246.6 MB,
в Git не хранится):

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

Публикация в GHCR выполняется GitHub Actions
(`.github/workflows/images.yml`) при push в `master` или по тегу `v*`.
Workflow скачивает БД из GitHub Release (с fallback-зеркалом), вшивает её в
API-образ и пушит `api` + `web` с тегами `1.0.0`, `latest`, `sha-<commit>`.

Если БД отсутствует в образе (например, сломанная локальная сборка),
healthcheck API не станет healthy, и `web` не запустится. Runtime-скачивания
БД нет — это осознанно: справочники должны поставляться внутри образа.

Проверка:

```bash
docker compose ps
curl -s http://localhost:8080/api/v1/health | python3 -m json.tool
curl -s http://localhost:8000/api/v1/health | python3 -m json.tool
```

nginx проксирует `/api/*` на бэкенд, поэтому фронтенд и API находятся на одном
origin — CORS для браузера не требуется.

Остановка:

```bash
docker compose down
```

### Локальная разработка без Docker

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Проверки:

```bash
ruff check app tests scripts
pytest -q
```

Swagger UI: `http://localhost:8000/docs`, ReDoc: `http://localhost:8000/redoc`.

Фронтенд отдельно:

```bash
cd jana-post-front
npm ci
npm run dev          # http://localhost:5173, /api проксируется на :8000
```

### Windows / CRLF

Если сборка падает с ошибкой вида:

```text
set: line 2: illegal option -
```

значит `.sh`-файлы попали в рабочую копию с CRLF-переводами строк (типично для
Git for Windows). В репозитории есть `.gitattributes`, который фиксирует LF для
`*.sh` и `Dockerfile`, а Dockerfile дополнительно очищает `\r` перед запуском
скриптов. После обновления:

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.build.yml build --no-cache api web
docker compose up -d
```

Если старые файлы всё ещё с CRLF:

```bash
git config core.autocrlf false
git rm --cached -r .
git reset --hard
```

Путь `fetch.sh (http://fetch.sh/)` из вывода Docker Desktop — это его
авто-ссылка в логе; на ошибку влияет именно CRLF, а не имя файла.

---

## REST API

### `GET /health` и `GET /api/v1/health`

Короткий алиас из ТЗ и основной эндпоинт возвращают одно и то же:

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/api/v1/health
```

```json
{
  "status": "ok",
  "version": "1.0.0",
  "db_loaded": true,
  "db_path": "/data/address_base.db",
  "db_file_exists": true,
  "fts": true,
  "provinces": 20,
  "cities": 238,
  "districts": 230,
  "streets": 49541
}
```

`db_loaded: false` означает fallback-режим: API продолжает работать на
regex-разборе с `confidence = 0.5`.

### `POST /api/v1/parse/batch`

Принимает **обе формы тела**:

```bash
# 1) JSON-массив
curl -s -X POST http://localhost:8000/api/v1/parse/batch \
  -H 'Content-Type: application/json' \
  -d '[
    {"id": "rec_001", "raw_address": "г. Актобе, ул. Абая, д. 55/6, кв 12"},
    {"id": "rec_002", "raw_address": "Алматы, Ауэзовский район, Казыбек би 12а, подъезд 4, этаж 9"}
  ]'

# 2) Объект с полем items
curl -s -X POST http://localhost:8000/api/v1/parse/batch \
  -H 'Content-Type: application/json' \
  -d '{
    "items": [
      {"id": "rec_003", "raw_address": "город Актобе/Ақтөбе қаласы, 12 микрорайон, дом 5, кв 3"}
    ]
  }'
```

Ответ — массив объектов **в том же порядке**:

```json
[
  {
    "id": "rec_001",
    "raw_address": "г. Актобе, ул. Абая, д. 55/6, кв 12",
    "province": "Актюбинская область",
    "city": "Актобе",
    "district": null,
    "street_type": "улица",
    "street_name": "Абая",
    "living_area": null,
    "landmark": null,
    "house": "55/6",
    "apartment": "12",
    "entrance": null,
    "floor": null,
    "confidence": 0.96
  }
]
```

Заголовки ответа:

| Заголовок | Пример | Назначение |
|---|---|---|
| `X-Records` | `2` | количество обработанных записей |
| `X-Elapsed-Ms` | `4.9` | время обработки пакета, мс |

Ошибки: `422` — невалидное тело/запись, `413` — пакет больше
`MAX_BATCH_SIZE` (по умолчанию 5000).

### CORS для фронтенда

`CORSMiddleware` включён: разрешены `*` origins, все методы и заголовки.
`X-Records` и `X-Elapsed-Ms` добавлены в `expose_headers`, поэтому фронтенд на
`localhost:3000`, `localhost:5173` или `localhost:8080` может читать их из JS.
В Docker-сборке запросы идут через nginx (`/api/*` → `api:8000`) на том же
origin. Для публичного продакшена список origins стоит сузить до конкретных
доменов.

### Поля ответа (14 полей ТЗ)

| Поле | Тип | Описание |
|---|---|---|
| `id` | string | идентификатор записи из запроса |
| `raw_address` | string | входная строка без изменений |
| `province` | string \| null | область из БД (`Актюбинская область`, `Алматы`) |
| `city` | string \| null | чистое имя города из БД, без «г.»/«город» |
| `district` | string \| null | район из БД без слова «район» |
| `street_type` | string \| null | `улица`, `проспект`, `микрорайон`, `квартал`, … |
| `street_name` | string \| null | слайс исходной строки |
| `living_area` | string \| null | слайс исходной строки (`12 микрорайон`) |
| `landmark` | string \| null | слайс исходной строки (`магазин Береке`, `Qazpost`) |
| `house` | string \| null | `55/6`, `12а` |
| `apartment` | string \| null | значение после `кв`, `квартира`, `flat`, `kv` |
| `entrance` | string \| null | значение после `под`, `подъезд` |
| `floor` | string \| null | значение после `эт`, `этаж` |
| `confidence` | float | уверенность разбора, `0…0.99` |

`living_area` и `street_name` взаимоисключающие: если сущность в БД помечена
как `living_area`, название попадает в `living_area`.

### Уверенность (`confidence`)

| Ситуация | Значение |
|---|---|
| База отсутствует (fallback) | `0.5` |
| База есть, сущность не найдена | `0.5` + `0.03` за каждый классификатор |
| Найдено, похожесть ≥ 0.95 | `0.9` + бонусы |
| Найдено, похожесть 0.8…0.95 / < 0.8 | `0.8` / `0.62` + бонусы |
| Неоднозначная улица без города | `0.5…0.6` |
| Пустая строка | `0.0` |

Бонус за определённые `province`/`city`/`district` — по `0.03`, максимум `0.99`.

---

## Веб-интерфейс

Интерфейс доступен на **http://localhost:8080** (`jana-post-front`, React 19 + Vite,
в Docker — nginx). Сценарий работы:

1. Загрузить JSON-массив `[{ "id": "...", "raw_address": "..." }]` или вставить адреса текстом.
2. Посмотреть разбор, отфильтровать записи с `confidence < 0.7`, при необходимости
   вручную исправить поля.
3. Скачать результат кнопкой **«Скачать JSON»** — файл всегда называется
   **`sample_output.json`** и содержит 14 полей ТЗ.

Запросы идут на `/api/v1/parse/batch`. В dev-режиме Vite проксирует `/api` на
`http://localhost:8000`, в Docker — nginx проксирует на `http://api:8000`.
Сессия сохраняется в `localStorage`, офлайн-карта работает без внешних CDN.

---

## Производительность

```bash
cd backend
python3 scripts/bench.py --count 1000 --db data/address_base.db --repeat 3
```

Результат на референсной машине (4 потока, холодный LRU-кэш):

```
прогон 1: 1.363 c (1.36 мс/запись)
прогон 2: 1.349 c (1.35 мс/запись)
прогон 3: 1.307 c (1.31 мс/запись)
Итого: min=1.307 c, медиана=1.349 c
Лимит ТЗ (300 c): OK
Заполнено хотя бы одно поле: 1000/1000
```

Запас к лимиту ТЗ — **~220×**. Повторные адреса обслуживаются LRU-кэшем,
поэтому реальный пакет с повторами обрабатывается ещё быстрее.

---

## Структура проекта

```
.
├── docker-compose.yml          # готовые образы api + web из GHCR
├── docker-compose.build.yml    # опциональная локальная сборка образов
├── .github/workflows/
│   └── images.yml              # сборка и публикация образов в GHCR
├── README.md
├── .gitignore                  # кэши, .venv, node_modules, логи; боевая БД не игнорируется
├── jana-post-front/            # React 19 + Vite UI
│   ├── Dockerfile              # Vite build → nginx + офлайн-тайлы
│   ├── nginx/default.conf.template  # SPA + proxy /api/* → API_UPSTREAM
│   ├── src/api/client.js       # API_BASE = /api/v1
│   ├── src/App.jsx             # экспорт sample_output.json
│   └── offline-map/            # скрипт загрузки PMTiles
├── backend/
│   ├── Dockerfile              # multi-stage, непривилегированный пользователь
│   ├── .dockerignore
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   ├── pyproject.toml          # pytest + ruff
│   ├── app/
│   │   ├── main.py             # FastAPI, CORS, GZip, lifespan
│   │   ├── config.py           # настройки из env
│   │   ├── models.py           # Pydantic-схемы (14 полей)
│   │   ├── api/v1.py           # parse/batch + health
│   │   ├── services/parser.py  # конвейер, LRU, ThreadPoolExecutor
│   │   ├── core/text.py        # WorkText, токенизация, fold казахских букв
│   │   ├── core/vocabulary.py  # regex-грамматики и словари
│   │   ├── core/tail_parser.py # дом/квартира/подъезд/этаж/ориентир
│   │   └── repositories/repository.py  # SQLite FTS5, fallback, hot-reload
│   ├── data/
│   │   ├── address_base.db     # вход для сборки образа (не в Git)
│   │   └── README.md           # как устроена доставка БД
│   ├── db/schema.sql           # эталонная схема v2
│   ├── scripts/
│   │   ├── bench.py            # замер производительности
│   │   └── make_demo_db.py     # демо-БД для тестов
│   ├── sample_output.json      # пример ответа
│   └── tests/                  # 86 pytest-тестов
└── base-builder/
    ├── build_osm_db.py         # offline ETL: OSM PBF + КАТО → address_base.db
    └── README.md
```

---

## Настройки (переменные окружения)

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `ADDRESS_DB_PATH` | `data/address_base.db` (в Docker — `/data/address_base.db`) | путь к вшитой в образ БД |
| `MAX_BATCH_SIZE` | `5000` | максимум записей в одном запросе |
| `PARSER_WORKERS` | `4` | потоки обработки пакета |
| `PARSER_CACHE_SIZE` | `200000` | размер LRU-кэша разобранных адресов |
| `DB_RELOAD_INTERVAL` | `5` | период проверки появления/обновления БД, сек |
| `LOG_LEVEL` | `INFO` | уровень логирования |
| `APP_NAME`, `APP_VERSION` | `Jana Address Parser`, `1.0.0` | метаданные сервиса |

---

## Данные и Docker

- `backend/data/address_base.db` (246.6 MB) **не хранится в Git**: GitHub
  отклоняет файлы больше 100 MB. Она вшивается в API-образ на этапе сборки:
  локально — из `backend/data/address_base.db`, в CI — из
  [GitHub Release v1.0](https://github.com/Hydr0x1de/posttech-hackathon/releases/tag/v1.0)
  (fallback-зеркало `posttech_db.hydr0x1de.xyz` используется только сборкой).
- `backend/Dockerfile` копирует `data/address_base.db` в `/data/address_base.db`
  внутри образа. В `docker-compose.yml` нет volume для БД и нет runtime-скачивания.
- `.dockerignore` **не исключает** `data/address_base.db` из build context:
  файл нужен для сборки образа. Остальные данные и тесты исключены.
- `web` собирается из `jana-post-front`, слушает `8080` и проксирует
  `/api/*` на `http://api:8000`. `depends_on` ждёт `service_healthy`.
- Healthcheck API строгий: HTTP 200 + `db_loaded: true`. Если БД не попала в
  образ, контейнер будет unhealthy, и `web` не запустится.
- SQLite открывается в режиме `mode=ro` с `PRAGMA query_only = 1`;
  WAL/SHM sidecar-файлы не создаются.

Схема v2 (`backend/db/schema.sql`) содержит `streets_and_areas.name_core_norm`
и FTS5-таблицу `fts_address`. При отсутствии FTS-индекса репозиторий
автоматически переходит на `LIKE`-поиск, а при отсутствии файла БД — на
`NullAddressRepository` с regex-разбором.

---

## Проверки качества

```bash
cd backend
ruff check app tests scripts   # All checks passed!
pytest -q                      # 86 passed
```

Тесты покрывают:

- точность слайсов и запрет исправления опечаток;
- Tail Parser и слитные написания;
- fallback без БД (`confidence = 0.5`);
- FTS5-поиск, `name_core_norm`, алиасы и казахские названия;
- защиту от неоднозначных гео-совпадений;
- API: обе формы тела, порядок ответа, заголовки, `413`/`422`;
- CORS preflight и `expose_headers`;
- health-алиасы и статистику БД.

---

## Ограничения

- Нечёткий поиск FTS5 работает по префиксам слов: опечатка внутри названия
  (`казибек` вместо `казыбек`) может не найтись в базе; ответ останется
  regex-разбором с `confidence = 0.5`, но исходное написание сохранится.
- Падежные формы классификаторов сопоставляются упрощённым стеммером
  (`Ауэзовского района` → `Ауэзовский`); для экзотических названий возможны
  промахи.
- Точность ограничена качеством OSM/КАТО-данных; ETL находится в
  `base-builder/build_osm_db.py`.
