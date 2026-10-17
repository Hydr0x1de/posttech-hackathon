"""Генератор демонстрационной `address_base.db` по эталонной схеме.

Нужен для локальной проверки и e2e-тестов, пока рабочая база не собрана.
Данные специально небольшие, но повторяют структуру и FTS5-индекс.

    python scripts/make_demo_db.py --out data/address_base.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.text import normalize  # noqa: E402
from app.core.vocabulary import canonical_street_type  # noqa: E402


def core_norm(name: str) -> str:
    """Ядро имени без базового слова типа ("ул. Абая" -> "абая")."""
    tokens = normalize(name).split()
    core = [token for token in tokens if canonical_street_type(token) is None]
    return " ".join(core) if core else " ".join(tokens)


SCHEMA = """
CREATE TABLE provinces (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE cities (
    id INTEGER PRIMARY KEY,
    province_id INTEGER,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    FOREIGN KEY(province_id) REFERENCES provinces(id)
);

CREATE TABLE districts (
    id INTEGER PRIMARY KEY,
    city_id INTEGER,
    province_id INTEGER,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL
);

CREATE TABLE streets_and_areas (
    id INTEGER PRIMARY KEY,
    city_id INTEGER,
    district_id INTEGER,
    province_id INTEGER,
    entity_type TEXT NOT NULL,
    street_type TEXT,
    name_official TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    name_core_norm TEXT,
    aliases TEXT,
    FOREIGN KEY(city_id) REFERENCES cities(id),
    FOREIGN KEY(district_id) REFERENCES districts(id)
);

CREATE VIRTUAL TABLE fts_address USING fts5(
    entity_id UNINDEXED,
    entity_type UNINDEXED,
    city_id UNINDEXED,
    district_id UNINDEXED,
    province_id UNINDEXED,
    street_type UNINDEXED,
    name_official UNINDEXED,
    search_text
);

CREATE INDEX idx_streets_city ON streets_and_areas(city_id);
CREATE INDEX idx_streets_name_norm ON streets_and_areas(name_norm);
CREATE INDEX idx_streets_core_norm ON streets_and_areas(name_core_norm);
"""

PROVINCES = [
    (1, "Алматы"),
    (2, "Астана"),
    (3, "Шымкент"),
    (4, "Актюбинская область"),
    (5, "Алматинская область"),
    (6, "Карагандинская область"),
    (7, "Туркестанская область"),
    (8, "Абай область"),
]

# (id, province_id, name)
CITIES = [
    (1, 1, "Алматы"),
    (2, 2, "Астана"),
    (3, 3, "Шымкент"),
    (4, 4, "Актобе"),
    (5, 6, "Караганда"),
    (6, 5, "Конаев"),
    (7, 7, "Туркестан"),
    (8, 6, "Балхаш"),
    (9, 8, "Семей"),
]

# (id, city_id, province_id, name) — без слова "район"
DISTRICTS = [
    (1, 1, 1, "Ауэзовский"),
    (2, 1, 1, "Бостандыкский"),
    (3, 1, 1, "Алматинский"),
    (4, 1, 1, "Медеуский"),
    (5, 2, 2, "Есильский"),
    (6, 2, 2, "Алматинский"),
    (7, 4, 4, "Астанинский"),
    (8, 5, 6, "Казыбекбийский"),
    (9, 6, 5, "Илийский"),
]

# (city_id, district_id, province_id, entity_type, street_type, name, aliases)
STREETS = [
    # Алматы
    (1, 1, 1, "street", "проспект", "Абая", None),
    (1, 1, 1, "street", "улица", "Казыбек би", None),
    (1, 1, 1, "street", "улица", "Кабанбай батыра", ["Фурманова", "Kabanbay batyr"]),
    (1, 2, 1, "street", "проспект", "Достык", ["Ленина"]),
    (1, 2, 1, "street", "улица", "Толе би", ["Толе-би"]),
    (1, 3, 1, "street", "улица", "Розыбакиева", None),
    (1, 4, 1, "street", "улица", "Виноградова", None),
    (1, None, 1, "living_area", "микрорайон", "Орбита-1", ["Орбита 1"]),
    (1, None, 1, "living_area", "микрорайон", "Коктем-3", ["Коктем 3"]),
    # Астана
    (2, 5, 2, "street", "улица", "Кунаева", None),
    (2, 5, 2, "street", "проспект", "Мангилик Ел", None),
    (2, 5, 2, "street", "улица", "Сарыарка", ["Сейфуллина"]),
    (2, 6, 2, "street", "улица", "Жубанова", None),
    (2, None, 2, "living_area", "микрорайон", "Алматы-2", ["Алматы 2"]),
    # Шымкент
    (3, None, 3, "street", "проспект", "Тауке хана", None),
    (3, None, 3, "street", "улица", "Байтурсынова", None),
    # Актобе
    (4, 7, 4, "street", "проспект", "Абилкайыр хана", ["Победы", "Абилкайыр хана проспект"]),
    (4, 7, 4, "street", "улица", "Аз Наурыз", None),
    (4, 7, 4, "street", "улица", "Маресьева", None),
    (4, 7, 4, "street", "улица", "Санкибай батыра", ["Санкибай батыр"]),
    (4, None, 4, "living_area", "микрорайон", "12 микрорайон", ["мкр 12"]),
    (4, None, 4, "living_area", "микрорайон", "1 микрорайон", ["мкр 1"]),
    # Караганда
    (5, 8, 6, "street", "проспект", "Бухар жырау", ["Бухар-жырау"]),
    (5, 8, 6, "street", "улица", "Ерубаева", None),
    (5, None, 6, "living_area", "микрорайон", "Восток-1", ["Восток 1"]),
    # Конаев / Балхаш / Туркестан / Семей
    (6, 9, 5, "street", "улица", "Пушкина", None),
    (7, None, 7, "street", "улица", "Жайлау", None),
    (8, None, 6, "street", "улица", "Деева", None),
    (9, None, 8, "street", "улица", "Кабанбай батыра", None),
]


def build(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO provinces(id, name) VALUES (?, ?)", PROVINCES)
        conn.executemany(
            "INSERT INTO cities(id, province_id, name, name_norm) VALUES (?, ?, ?, ?)",
            [(cid, pid, name, normalize(name)) for cid, pid, name in CITIES],
        )
        conn.executemany(
            "INSERT INTO districts(id, city_id, province_id, name, name_norm)"
            " VALUES (?, ?, ?, ?, ?)",
            [(did, cid, pid, name, normalize(name)) for did, cid, pid, name in DISTRICTS],
        )

        fts_rows = []
        next_id = 1
        for city_id, district_id, province_id, entity_type, street_type, name, aliases in STREETS:
            entity_id = next_id
            next_id += 1
            norm = normalize(name)
            core = core_norm(name)
            alias_list = [normalize(a) for a in (aliases or [])]
            conn.execute(
                """INSERT INTO streets_and_areas
                   (id, city_id, district_id, province_id, entity_type, street_type,
                    name_official, name_norm, name_core_norm, aliases)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entity_id, city_id, district_id, province_id, entity_type,
                    street_type, name, norm, core,
                    json.dumps(alias_list, ensure_ascii=False) if alias_list else None,
                ),
            )
            search_text = " ".join([norm, core, *alias_list])
            fts_rows.append(
                (str(entity_id), entity_type, city_id, district_id, province_id,
                 street_type, name, search_text)
            )
        conn.executemany(
            """INSERT INTO fts_address
               (entity_id, entity_type, city_id, district_id, province_id,
                street_type, name_official, search_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            fts_rows,
        )
        conn.commit()
    finally:
        conn.close()
    print(f"База создана: {path} (provinces={len(PROVINCES)}, cities={len(CITIES)}, "
          f"districts={len(DISTRICTS)}, streets={len(STREETS)})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Сборка демонстрационной address_base.db")
    parser.add_argument("--out", default="data/address_base.db", help="путь к файлу БД")
    args = parser.parse_args()
    build(Path(args.out))


if __name__ == "__main__":
    main()
