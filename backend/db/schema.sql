-- Эталонная схема базы адресов (SQLite + FTS5).
-- Файл address_base.db формируется отдельным пайплайном подготовки данных;
-- сервис только читает его (mode=ro / PRAGMA query_only).

CREATE TABLE IF NOT EXISTS provinces (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cities (
    id INTEGER PRIMARY KEY,
    province_id INTEGER,
    name TEXT NOT NULL,          -- Чистое имя ("Актобе")
    name_norm TEXT NOT NULL,
    FOREIGN KEY(province_id) REFERENCES provinces(id)
);

CREATE TABLE IF NOT EXISTS districts (
    id INTEGER PRIMARY KEY,
    city_id INTEGER,
    province_id INTEGER,
    name TEXT NOT NULL,          -- Без слова "район" ("Ауэзовский")
    name_norm TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS streets_and_areas (
    id INTEGER PRIMARY KEY,
    city_id INTEGER,
    district_id INTEGER,
    province_id INTEGER,
    entity_type TEXT NOT NULL,   -- 'street' или 'living_area'
    street_type TEXT,            -- 'улица', 'проспект', 'микрорайон'
    name_official TEXT NOT NULL,
    name_norm TEXT NOT NULL,     -- Полное нормализованное имя с типом
    name_core_norm TEXT,         -- Ядро имени без типа ("нурсултана назарбаева")
    aliases TEXT,                -- JSON array старых названий: ["фурманова"]
    FOREIGN KEY(city_id) REFERENCES cities(id),
    FOREIGN KEY(district_id) REFERENCES districts(id)
);

-- Полнотекстовый индекс для нечёткого быстрого поиска
CREATE VIRTUAL TABLE IF NOT EXISTS fts_address USING fts5(
    entity_id UNINDEXED,
    entity_type UNINDEXED,
    city_id UNINDEXED,
    district_id UNINDEXED,
    province_id UNINDEXED,
    street_type UNINDEXED,
    name_official UNINDEXED,
    search_text                  -- name_norm + aliases
);

-- Дома с координатами — единственный источник точек для геокодинга.
CREATE TABLE IF NOT EXISTS houses (
    id INTEGER PRIMARY KEY,
    street_id INTEGER,
    city_id INTEGER,
    district_id INTEGER,
    province_id INTEGER,
    housenumber TEXT,
    housenumber_norm TEXT,       -- лоссовый ключ: "1/2" -> "12"
    street_norm TEXT,
    street_core_norm TEXT,
    street_raw TEXT,
    place_norm TEXT,
    addr_city_norm TEXT,
    place_id INTEGER,
    lat REAL,
    lon REAL,
    building TEXT,
    levels INTEGER
);

-- Индексы, ускоряющие обогащение и поиск родительских сущностей
CREATE INDEX IF NOT EXISTS idx_cities_province ON cities(province_id);
CREATE INDEX IF NOT EXISTS idx_cities_name_norm ON cities(name_norm);
CREATE INDEX IF NOT EXISTS idx_districts_city ON districts(city_id);
CREATE INDEX IF NOT EXISTS idx_districts_name_norm ON districts(name_norm);
CREATE INDEX IF NOT EXISTS idx_streets_city ON streets_and_areas(city_id);
CREATE INDEX IF NOT EXISTS idx_streets_district ON streets_and_areas(district_id);
CREATE INDEX IF NOT EXISTS idx_streets_name_norm ON streets_and_areas(name_norm);
CREATE INDEX IF NOT EXISTS idx_streets_core_norm ON streets_and_areas(name_core_norm);

-- Индексы геокодинга (ТЗ, разделы 2 и 7).
CREATE INDEX IF NOT EXISTS idx_houses_street ON houses(street_id);
CREATE INDEX IF NOT EXISTS idx_houses_city ON houses(city_id);
CREATE INDEX IF NOT EXISTS idx_houses_district ON houses(district_id);
CREATE INDEX IF NOT EXISTS idx_houses_norm ON houses(housenumber_norm);
