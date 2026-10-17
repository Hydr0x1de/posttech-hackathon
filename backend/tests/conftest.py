"""Общие фикстуры: демонстрационная БД во временном каталоге."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.make_demo_db import build  # noqa: E402


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Демо-база, собранная по эталонной схеме (SQLite + FTS5)."""
    path = tmp_path_factory.mktemp("db") / "address_base.db"
    build(path)
    return path


@pytest.fixture(scope="session")
def truncated_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """База с «куцым» name_norm и сущностью, непохожей на запрос.

    * `name_norm = "12"` при `name_official = "12 микрорайон"`;
    * `"66-й квартал"` — для проверки порога похожести.
    """
    import sqlite3

    from app.core.text import normalize

    path = tmp_path_factory.mktemp("db_truncated") / "address_base.db"
    schema = (Path(__file__).resolve().parents[1] / "db" / "schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.execute("INSERT INTO provinces(id, name) VALUES (1, 'Карагандинская область')")
    conn.execute(
        "INSERT INTO cities(id, province_id, name, name_norm) VALUES (5, 1, 'Караганда', ?)",
        (normalize("Караганда"),),
    )
    conn.execute(
        "INSERT INTO districts(id, city_id, province_id, name, name_norm)"
        " VALUES (8, 5, 1, 'Казыбекбийский', ?)",
        (normalize("Казыбекбийский"),),
    )
    rows = [
        (1, "12 микрорайон", "12", "living_area", "12"),
        (2, "66-й квартал", "66 й квартал", "living_area", "66 й квартал"),
    ]
    for entity_id, official, name_norm, entity_type, search_text in rows:
        conn.execute(
            "INSERT INTO streets_and_areas (id, city_id, district_id, province_id,"
            " entity_type, street_type, name_official, name_norm, aliases)"
            " VALUES (?, 5, 8, 1, ?, 'микрорайон', ?, ?, NULL)",
            (entity_id, entity_type, official, name_norm),
        )
        conn.execute(
            "INSERT INTO fts_address (entity_id, entity_type, city_id, district_id,"
            " province_id, street_type, name_official, search_text)"
            " VALUES (?, ?, 5, 8, 1, 'микрорайон', ?, ?)",
            (str(entity_id), entity_type, official, search_text),
        )
    conn.commit()
    conn.close()
    return path
