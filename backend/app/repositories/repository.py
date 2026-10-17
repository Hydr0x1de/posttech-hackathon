"""Слой доступа к `address_base.db` (Repository Pattern).

Ключевая особенность: файл базы формируется отдельным пайплайном подготовки
данных и на момент запуска сервиса может отсутствовать или быть неполным. Поэтому:

* `NullAddressRepository`  — полностью рабочий fallback: парсер отдаёт
  Regex-разбор с `confidence = 0.5` (требование ТЗ);
* `SQLiteAddressRepository` — реальный доступ к SQLite/FTS5, только чтение
  (`mode=ro`, `PRAGMA query_only`), поэтому БД можно монтировать в контейнер
  файлом и не бояться случайной записи;
* `RepositoryManager` — ленивая (пере)загрузка: если файл появился позже,
  сервис подхватит его без рестарта.

Соединения — thread-local: пакет обрабатывается в пуле потоков, а объект
`sqlite3.Connection` не потокобезопасен.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.core.text import fold_kazakh, normalize, tokenize

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Модели слоя доступа
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Classifier:
    """Справочник: область / город / район."""

    id: int
    name: str
    norm: str
    parent_id: int | None = None


@dataclass(frozen=True, slots=True)
class StreetEntity:
    """Улица или жилой массив из `streets_and_areas`."""

    id: int
    entity_type: str  # 'street' | 'living_area'
    name_official: str
    name_norm: str
    # Ядро имени без типа ("нурсултана назарбаева" / "12").
    # В схеме v2 хранится отдельной колонкой и обязательно участвует
    # в сопоставлении: пользователь обычно пишет адрес БЕЗ базового слова.
    name_core_norm: str | None
    street_type: str | None
    city_id: int | None
    district_id: int | None
    province_id: int | None
    aliases: tuple[str, ...] = ()
    # Все варианты написания для слайсинга (полное имя и старые названия).
    # normalize(name_official) обязателен: если в БД куцый name_norm
    # ("12" вместо "12 микрорайон"), слайсер должен видеть полное название.
    names: tuple[str, ...] = ()


@dataclass(slots=True)
class ClassifierIndex:
    provinces: list[Classifier] = field(default_factory=list)
    cities: list[Classifier] = field(default_factory=list)
    districts: list[Classifier] = field(default_factory=list)

    # ключ (стемминг-нормализованное имя) -> классификаторы
    province_keys: dict[str, list[Classifier]] = field(default_factory=dict)
    city_keys: dict[str, list[Classifier]] = field(default_factory=dict)
    district_keys: dict[str, list[Classifier]] = field(default_factory=dict)

    province_by_id: dict[int, Classifier] = field(default_factory=dict)
    city_by_id: dict[int, Classifier] = field(default_factory=dict)
    district_by_id: dict[int, Classifier] = field(default_factory=dict)
    # нормализованное имя города -> город (для авто-обогащения province)
    city_by_norm: dict[str, Classifier] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not (self.provinces or self.cities or self.districts)


# Города республиканского значения: провинция совпадает с городом (ТЗ, п.3).
REPUBLICAN_CITIES = frozenset({"алматы", "астана", "шымкент", "нур-султан", "аstana"})

# --------------------------------------------------------------------------- #
# Стемминг для сопоставления падежных форм ("Ауэзовского района" -> "Ауэзовский")
# --------------------------------------------------------------------------- #

_ENDINGS = (
    "ого", "ому", "ыми", "ими", "ого", "его", "ей", "ой", "ые", "ых", "ий", "ый",
    "ая", "ое", "ую", "юю", "ям", "ам", "ах", "ях", "ов", "ев", "ия", "ии", "ию",
    "ем", "ом", "ды", "ді", "ты", "ті", "сы", "сі", "ға", "ге", "қа", "ке", "ны",
    "ні", "а", "я", "ы", "и", "е", "у", "ю", "о", "й", "і",
)
_MIN_STEM = 3


@lru_cache(maxsize=4096)
def stem(token: str) -> str:
    """Очень лёгкий стеммер: срезает падежные окончания (максимум 2 шага)."""
    result = token
    for _ in range(2):
        for ending in _ENDINGS:
            if result.endswith(ending) and len(result) - len(ending) >= _MIN_STEM:
                result = result[: -len(ending)]
                break
        else:
            break
    return result


def _key(tokens: Iterable[str], *, drop_stop: set[str] | None = None) -> str:
    prepared = [stem(t) for t in tokens if t and (drop_stop is None or t not in drop_stop)]
    return " ".join(prepared)


# --------------------------------------------------------------------------- #
# Протокол
# --------------------------------------------------------------------------- #


@runtime_checkable
class AddressRepository(Protocol):
    db_loaded: bool
    db_path: str | None
    has_fts: bool

    def classifiers(self) -> ClassifierIndex: ...

    def search_streets(
        self,
        tokens: Sequence[str],
        *,
        city_ids: Sequence[int] | None = None,
        district_ids: Sequence[int] | None = None,
        limit: int = 8,
    ) -> list[StreetEntity]: ...

    def stats(self) -> dict[str, object]: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------- #
# Fallback: БД отсутствует
# --------------------------------------------------------------------------- #


class NullAddressRepository:
    """Заглушка: база ещё не собрана. Парсер работает на регулярках (c=0.5)."""

    db_loaded = False
    db_path: str | None = None
    has_fts = False

    def __init__(self) -> None:
        self._index = ClassifierIndex()

    def classifiers(self) -> ClassifierIndex:
        return self._index

    def search_streets(self, *args: object, **kwargs: object) -> list[StreetEntity]:
        return []

    def stats(self) -> dict[str, object]:
        return {
            "db_loaded": False,
            "db_path": self.db_path,
            "fts": False,
            "provinces": 0,
            "cities": 0,
            "districts": 0,
            "streets": 0,
        }

    def close(self) -> None:  # pragma: no cover
        return None


# --------------------------------------------------------------------------- #
# SQLite + FTS5
# --------------------------------------------------------------------------- #

_FTS_SAFE_RE = re.compile(r"[0-9a-zа-яёәғқңөұүһі]+")


class SQLiteAddressRepository:
    """Чтение `address_base.db` (SQLite + FTS5) без записи."""

    db_loaded = True

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.db_path = str(path)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._index = ClassifierIndex()
        self._has_fts = False
        self._has_core_norm = False
        self._street_count = 0
        self._load_metadata()

    # -- соединения -------------------------------------------------------- #

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            path = Path(self.db_path).as_posix()
            try:
                conn = sqlite3.connect(
                    f"file:{path}?mode=ro", uri=True, timeout=10.0, check_same_thread=True
                )
                conn.execute("PRAGMA query_only = 1")
            except sqlite3.OperationalError:
                # WAL-база в read-only каталоге: открываем обычным соединением,
                # но остаёмся только на чтение.
                conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=True)
                conn.execute("PRAGMA query_only = 1")
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA temp_store = MEMORY")
            self._local.conn = conn
        return conn

    def close(self) -> None:  # pragma: no cover
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- метаданные -------------------------------------------------------- #

    @property
    def has_fts(self) -> bool:
        return self._has_fts

    def _load_metadata(self) -> None:
        conn = self._connect()
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        }
        self._has_fts = "fts_address" in tables
        if not self._has_fts:
            logger.warning("В %s нет таблицы fts_address — поиск пойдёт через LIKE", self.db_path)

        # Схема v2 добавила name_core_norm. Старые/демо-базы могут быть без
        # неё — тогда деградируем до name_norm, не падая на SELECT.
        if "streets_and_areas" in tables:
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(streets_and_areas)")
            }
            self._has_core_norm = "name_core_norm" in columns

        index = ClassifierIndex()
        if "provinces" in tables:
            for row in conn.execute("SELECT id, name FROM provinces"):
                item = Classifier(id=row["id"], name=row["name"], norm=normalize(row["name"]))
                index.provinces.append(item)
                index.province_by_id[item.id] = item
        if "cities" in tables:
            for row in conn.execute("SELECT id, province_id, name FROM cities"):
                item = Classifier(
                    id=row["id"], name=row["name"], norm=normalize(row["name"]),
                    parent_id=row["province_id"],
                )
                index.cities.append(item)
                index.city_by_id[item.id] = item
                index.city_by_norm[item.norm] = item
        if "districts" in tables:
            for row in conn.execute("SELECT id, city_id, province_id, name FROM districts"):
                item = Classifier(
                    id=row["id"], name=row["name"], norm=normalize(row["name"]),
                    parent_id=row["city_id"] or row["province_id"],
                )
                index.districts.append(item)
                index.district_by_id[item.id] = item

        for items, target in (
            (index.provinces, index.province_keys),
            (index.cities, index.city_keys),
            (index.districts, index.district_keys),
        ):
            for item in items:
                for key in classifier_keys(item.norm):
                    target.setdefault(key, []).append(item)

        try:
            row = conn.execute("SELECT count(*) AS c FROM streets_and_areas").fetchone()
            self._street_count = row["c"]
        except sqlite3.Error:  # pragma: no cover - защита от неполной БД
            self._street_count = 0

        self._index = index
        logger.info(
            "БД загружена: %s (provinces=%d, cities=%d, districts=%d, streets=%d, fts=%s)",
            self.db_path, len(index.provinces), len(index.cities),
            len(index.districts), self._street_count, self._has_fts,
        )

    def classifiers(self) -> ClassifierIndex:
        return self._index

    def stats(self) -> dict[str, object]:
        index = self._index
        return {
            "db_loaded": True,
            "db_path": self.db_path,
            "fts": self._has_fts,
            "provinces": len(index.provinces),
            "cities": len(index.cities),
            "districts": len(index.districts),
            "streets": self._street_count,
        }

    # -- поиск улиц -------------------------------------------------------- #

    def search_streets(
        self,
        tokens: Sequence[str],
        *,
        city_ids: Sequence[int] | None = None,
        district_ids: Sequence[int] | None = None,
        limit: int = 8,
    ) -> list[StreetEntity]:
        cleaned: list[str] = []
        for token in tokens:
            for part in _FTS_SAFE_RE.findall(token.lower().replace("ё", "е")):
                if len(part) >= 2 and part not in CLASSIFIER_STOP:
                    cleaned.append(part)
        if not cleaned:
            return []

        # Порядок токенов сохраняем; постепенно ослабляем запрос, а в конце
        # уходим в OR с ранжированием bm25: лишние слова (например, казахский
        # вариант названия в двуязычной склейке) не должны блокировать поиск.
        ordered = cleaned[:8]
        # Казахские названия индексируются в исходном виде, но в search_text
        # попадают и русские алиасы. Дополнительный проход по свёрнутым буквам
        # позволяет найти "Мәңгілік Ел" по записи "Мангилик ел".
        folded = [fold_kazakh(token) for token in ordered]
        folded = list(dict.fromkeys(folded))
        token_groups = [ordered]
        if folded != ordered:
            token_groups.append(folded)

        queries: list[str] = []
        strict_queries: set[str] = set()
        for group in token_groups:
            if group:
                strict = " AND ".join(f'"{t}"*' for t in group)
                strict_queries.add(strict)
                queries.append(strict)
            for subset in (group[:4], group[:2]):
                if subset and subset != group:
                    queries.append(" AND ".join(f'"{t}"*' for t in subset))
            if len(group) > 1:
                queries.append(" OR ".join(f'"{t}"*' for t in group))

        conn = self._connect()
        seen_queries: set[str] = set()
        # Полный строгий AND обычно сразу даёт точное название. Если же он
        # пуст, а ослабленный AND что-то нашёл, продолжаем до OR: иначе
        # "жк аксай" может вернуть менее полное "Аксай" вместо "Аксай-3А".
        collected: dict[int, StreetEntity] = {}
        for match in queries:
            if match in seen_queries:
                continue
            seen_queries.add(match)
            rows = self._query(conn, match, city_ids, district_ids, limit)
            for entity in rows:
                collected.setdefault(entity.id, entity)
            if rows and match in strict_queries:
                break
            if len(collected) >= limit:
                break
        return list(collected.values())[:limit]

    def _query(
        self,
        conn: sqlite3.Connection,
        match: str,
        city_ids: Sequence[int] | None,
        district_ids: Sequence[int] | None,
        limit: int,
    ) -> list[StreetEntity]:
        try:
            ids = (
                self._query_fts(conn, match, city_ids, district_ids, limit)
                if self._has_fts
                else self._query_like(conn, match, limit)
            )
        except sqlite3.Error as exc:  # повреждённая/недописанная БД не должна ронять сервис
            logger.warning("Ошибка поиска в БД (%s): %s", self.db_path, exc)
            return []
        if not ids:
            return []
        return self._fetch_entities(conn, ids)

    def _query_fts(
        self,
        conn: sqlite3.Connection,
        match: str,
        city_ids: Sequence[int] | None,
        district_ids: Sequence[int] | None,
        limit: int,
    ) -> list[int]:
        sql = "SELECT entity_id FROM fts_address WHERE fts_address MATCH ?"
        params: list[object] = [match]
        if city_ids:
            sql += f" AND city_id IN ({','.join('?' * len(city_ids))})"
            params.extend(city_ids)
        if district_ids:
            sql += f" AND district_id IN ({','.join('?' * len(district_ids))})"
            params.extend(district_ids)
        sql += f" ORDER BY rank LIMIT {int(limit)}"
        return [_as_int(row["entity_id"]) for row in conn.execute(sql, params)]

    def _query_like(
        self,
        conn: sqlite3.Connection,
        match: str,
        limit: int,
    ) -> list[int]:
        """Fallback, если FTS-индекс ещё не построен."""
        parts = {part for part in _FTS_SAFE_RE.findall(match.lower()) if len(part) >= 2}
        longest = sorted(parts, key=len, reverse=True)[:3]
        if not longest:
            return []
        where = " AND ".join(
            (
                "(name_norm LIKE ? OR ifnull(name_core_norm,'') LIKE ?"
                " OR ifnull(aliases,'') LIKE ?)"
                if self._has_core_norm
                else "(name_norm LIKE ? OR ifnull(aliases,'') LIKE ?)"
            )
            for _ in longest
        )
        params: list[object] = []
        for token in longest:
            if self._has_core_norm:
                params.extend([f"%{token}%", f"%{token}%", f"%{token}%"])
            else:
                params.extend([f"%{token}%", f"%{token}%"])
        sql = f"SELECT id FROM streets_and_areas WHERE {where} LIMIT {int(limit)}"
        return [row["id"] for row in conn.execute(sql, params)]

    def _fetch_entities(
        self, conn: sqlite3.Connection, ids: Sequence[int]
    ) -> list[StreetEntity]:
        placeholders = ",".join("?" * len(ids))
        core_expr = "name_core_norm" if self._has_core_norm else "NULL AS name_core_norm"
        rows = conn.execute(
            f"""SELECT id, city_id, district_id, province_id, entity_type, street_type,
                       name_official, name_norm, {core_expr}, aliases
                FROM streets_and_areas WHERE id IN ({placeholders})""",
            list(ids),
        ).fetchall()
        by_id: dict[int, StreetEntity] = {}
        for row in rows:
            raw_aliases = row["aliases"]
            aliases: tuple[str, ...] = ()
            if raw_aliases:
                try:
                    parsed = json.loads(raw_aliases)
                    if isinstance(parsed, list):
                        aliases = tuple(
                            normalize(str(item)) for item in parsed if str(item).strip()
                        )
                except (json.JSONDecodeError, TypeError):
                    aliases = tuple(
                        normalize(part)
                        for part in re.split(r"[,;]", str(raw_aliases))
                        if part.strip()
                    )
            official = row["name_official"] or ""
            official_norm = normalize(official)
            name_norm = row["name_norm"] or official_norm
            name_core_norm = normalize(row["name_core_norm"] or "") or None
            # dedup с сохранением порядка: name_norm, официальное, старые названия
            names = tuple(
                dict.fromkeys(n for n in (name_norm, official_norm, *aliases) if n)
            )
            by_id[row["id"]] = StreetEntity(
                id=row["id"],
                entity_type=row["entity_type"] or "street",
                name_official=official,
                name_norm=name_norm,
                name_core_norm=name_core_norm,
                street_type=row["street_type"],
                city_id=row["city_id"],
                district_id=row["district_id"],
                province_id=row["province_id"],
                aliases=aliases,
                names=names,
            )
        # Сохраняем порядок релевантности из FTS.
        return [by_id[i] for i in ids if i in by_id]


def _as_int(value: object) -> int:
    """entity_id в FTS5-колонке UNINDEXED обычно хранится текстом."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return -1


# Служебные слова, которые не должны попадать в ключи справочников.
CLASSIFIER_STOP = frozenset(
    {
        "область", "облысы", "обл", "район", "ауданы", "аудан", "город", "г", "қаласы",
        "каласы", "рк", "кз", "казахстан", "республикасы", "республика",
    }
)


def _name_variants(tokens: list[str], stop: frozenset[str]) -> list[list[str]]:
    """Полное имя и имя без служебных слов: "актюбинская область" / "актюбинская"."""
    variants = [tokens]
    stripped = [t for t in tokens if t not in stop]
    if stripped and stripped != tokens:
        variants.append(stripped)
    return variants


@lru_cache(maxsize=16_384)
def classifier_keys(name: str) -> tuple[str, ...]:
    """Ключи сопоставления классификатора: полный и без служебных слов.

    Используется и при построении словаря из БД, и при поиске в тексте адреса
    (одна и та же функция гарантирует симметричность ключей).
    """
    tokens = tokenize(name) or [name]
    return tuple(_key(variant) for variant in _name_variants(tokens, CLASSIFIER_STOP))


# --------------------------------------------------------------------------- #
# Менеджер: ленивая загрузка и hot-reload появившегося файла БД
# --------------------------------------------------------------------------- #


class RepositoryManager:
    """Отдаёт актуальный репозиторий; умеет подхватить появившуюся БД."""

    def __init__(self, db_path: str, *, reload_interval: float = 5.0) -> None:
        self._path = db_path
        self._reload_interval = reload_interval
        self._repo: AddressRepository | NullAddressRepository = NullAddressRepository()
        self._last_check = 0.0
        self._mtime: float | None = None
        self._lock = threading.Lock()
        self.refresh(force=True)

    # -- api --------------------------------------------------------------- #

    @property
    def path(self) -> str:
        return self._path

    def get(self) -> AddressRepository | NullAddressRepository:
        """Репозиторий; при появлении/обновлении файла БД — перезагрузка."""
        self.refresh()
        return self._repo

    def health(self) -> dict[str, object]:
        repo = self.get()
        payload = repo.stats()
        payload["db_file_exists"] = os.path.exists(self._path)
        payload["db_mtime"] = self._mtime
        return payload

    def refresh(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_check < self._reload_interval:
            return
        with self._lock:
            if not force and now - self._last_check < self._reload_interval:
                return
            self._last_check = now
            try:
                mtime = os.path.getmtime(self._path)
            except OSError:
                if not isinstance(self._repo, NullAddressRepository):
                    logger.warning("БД %s пропала — перехожу в fallback-режим", self._path)
                    self._repo = NullAddressRepository()
                    self._mtime = None
                return
            if isinstance(self._repo, SQLiteAddressRepository) and self._mtime == mtime:
                return
            try:
                self._repo = SQLiteAddressRepository(self._path)
                self._mtime = mtime
            except sqlite3.Error as exc:
                logger.error("Не удалось открыть БД %s: %s (fallback)", self._path, exc)
                self._repo = NullAddressRepository()
                self._mtime = None

    def close(self) -> None:  # pragma: no cover
        self._repo.close()


__all__ = [
    "AddressRepository",
    "CLASSIFIER_STOP",
    "Classifier",
    "ClassifierIndex",
    "NullAddressRepository",
    "REPUBLICAN_CITIES",
    "RepositoryManager",
    "SQLiteAddressRepository",
    "StreetEntity",
    "classifier_keys",
    "stem",
]
