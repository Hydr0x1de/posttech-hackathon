"""Тесты слоя данных: FTS5-поиск, fallback, hot-reload базы."""

from __future__ import annotations

import json
import shutil
import sqlite3

import pytest

from app.repositories.repository import (
    NullAddressRepository,
    RepositoryManager,
    SQLiteAddressRepository,
    classifier_keys,
    stem,
)
from app.services.parser import AddressParserService


def test_stats(demo_db):
    repo = SQLiteAddressRepository(demo_db)
    stats = repo.stats()
    assert stats["db_loaded"] is True
    assert stats["fts"] is True
    assert stats["streets"] > 0
    repo.close()


def test_search_by_name_and_alias(demo_db):
    repo = SQLiteAddressRepository(demo_db)
    by_name = repo.search_streets(["казыбек", "би"])
    assert by_name and by_name[0].name_official == "Казыбек би"
    by_alias = repo.search_streets(["фурманова"])
    assert by_alias and by_alias[0].name_official == "Кабанбай батыра"
    assert repo.search_streets(["нетакой", "улицы"]) == []
    repo.close()


def test_search_filtered_by_city(demo_db):
    repo = SQLiteAddressRepository(demo_db)
    # "Кабанбай батыра" есть и в Алматы, и в Семее
    almaty = repo.search_streets(["кабанбай", "батыра"], city_ids=[1])
    semey = repo.search_streets(["кабанбай", "батыра"], city_ids=[9])
    assert almaty and almaty[0].city_id == 1
    assert semey and semey[0].city_id == 9
    repo.close()


def test_classifier_index_loaded(demo_db):
    repo = SQLiteAddressRepository(demo_db)
    index = repo.classifiers()
    names = {item.name for item in index.districts}
    assert "Ауэзовский" in names
    assert all("район" not in name.lower() for name in names)
    assert index.city_keys  # ключи для падежных форм
    repo.close()


def test_like_fallback_without_fts(tmp_path, demo_db):
    """База без таблицы fts_address (ещё не проиндексирована)."""
    raw = tmp_path / "no_fts.db"
    shutil.copy2(demo_db, raw)
    connection = sqlite3.connect(raw)
    connection.execute("DROP TABLE fts_address")
    connection.commit()
    connection.close()

    repo = SQLiteAddressRepository(raw)
    assert repo.has_fts is False
    found = repo.search_streets(["казыбек"])
    assert found and found[0].name_official == "Казыбек би"
    repo.close()


def test_missing_db_falls_back(tmp_path):
    manager = RepositoryManager(str(tmp_path / "nope.db"), reload_interval=0)
    assert manager.health()["db_loaded"] is False
    assert isinstance(manager.get(), NullAddressRepository)


def test_hot_reload_when_db_appears(tmp_path, demo_db):
    """База появилась после старта — подхватывается без рестарта."""
    target = tmp_path / "late.db"
    manager = RepositoryManager(str(target), reload_interval=0)
    service = AddressParserService(manager.get)
    assert manager.get().db_loaded is False

    shutil.copy2(demo_db, target)

    manager.refresh(force=True)
    assert manager.get().db_loaded is True
    result = service.parse("rec_1", "Алматы, Ауэзовский район, Казыбек би 12а")
    assert result.city == "Алматы"
    assert result.district == "Ауэзовский"
    assert result.confidence > 0.9


def test_aliases_json_parsed(demo_db):
    repo = SQLiteAddressRepository(demo_db)
    entity = repo.search_streets(["фурманова"])[0]
    assert entity.aliases == ("фурманова", "kabanbay batyr")
    assert json.dumps(list(entity.aliases), ensure_ascii=False)
    repo.close()


def test_resolve_streets_finds_kazakh_duplicate(turan_db):
    """resolve_streets(«туран») видит и «Тұран даңғылы», и «проспект Туран»."""
    repo = SQLiteAddressRepository(turan_db)
    found = repo.resolve_streets("туран", "туран", city_id=2)
    assert {entity.id for entity in found} >= {1907, 27127}
    repo.close()


def test_resolve_streets_finds_kazakh_duplicate_without_fts(tmp_path, turan_db):
    """Без FTS широкий скан по городу всё равно находит оба дубля."""
    raw = tmp_path / "turan_no_fts.db"
    shutil.copy2(turan_db, raw)
    connection = sqlite3.connect(raw)
    connection.execute("DROP TABLE fts_address")
    connection.commit()
    connection.close()

    repo = SQLiteAddressRepository(raw)
    assert repo.has_fts is False
    found = repo.resolve_streets("туран", "туран", city_id=2)
    assert {entity.id for entity in found} >= {1907, 27127}
    repo.close()


def test_broken_db_is_not_fatal(tmp_path):
    path = tmp_path / "broken.db"
    path.write_bytes(b"not a sqlite file at all")
    with pytest.raises(sqlite3.DatabaseError):
        SQLiteAddressRepository(path)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Ауэзовский", "ауэзовск"),
        ("Ауэзовского", "ауэзовск"),
        ("Актобе", "актоб"),
        ("Актюбинская область", "актюбинск область"),
    ],
)
def test_classifier_keys(name, expected):
    assert classifier_keys(name)[0] == expected


def test_stem_keeps_short_tokens():
    assert stem("ус") == "ус"
    # падежные формы коротких названий приводятся к одной основе
    assert stem("абая") == stem("абай") == "аба"
