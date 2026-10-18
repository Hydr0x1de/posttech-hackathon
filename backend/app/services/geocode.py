"""Автономный forward-геокодинг по вшитой в образ `address_base.db`.

Координаты есть только в `houses`, поэтому алгоритм такой:

1. адрес разбирается существующим `AddressParserService` (или берётся из
   уже разобранных полей запроса);
2. дом ищется по `street_id` + лоссовому `housenumber_norm`, после чего номер
   строго проверяется в Python (`1/2` не должен совпасть с `12`);
3. если дома нет — центроид улицы/жилмассива, затем района, города, области;
4. центроиды города/района/области кэшируются в процессе, чтобы не сканировать
   1.3 млн строк на каждый запрос.

SQLite синхронный: HTTP-слой вызывает сервис через `run_in_threadpool`.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.core import vocabulary as vocab
from app.core.text import fold_kazakh, normalize
from app.repositories.repository import (
    AddressRepository,
    Centroid,
    HousePoint,
    NullAddressRepository,
    StreetEntity,
)
from app.services.parser import AddressParserService

logger = logging.getLogger(__name__)

# Допустимые значения precision (см. ТЗ).
PRECISIONS = frozenset(
    {
        "house",
        "house_approx",
        "street",
        "living_area",
        "district",
        "city",
        "province",
    }
)

_SLASH_SPACES_RE = re.compile(r"\s*/\s*")
_HOUSE_STRIP_RE = re.compile(r"[^0-9a-zа-яёәғқңөұүһі/\-]+")
_HOUSE_LOOKUP_RE = re.compile(r"[^0-9a-zа-яёәғқңөұүһі]+")


def normalize_house(value: str | None) -> str:
    """Строгая нормализация номера дома — сохраняет `/` и литеры.

    `housenumber_norm` в БД лоссовый (`1/2` -> `12`, `55/6` -> `556`),
    поэтому финальное решение принимается по этой функции:

    * `"55 / 6"` -> `"55/6"`
    * `"12А"` -> `"12а"`
    * `"1/2"` -> `"1/2"`
    * `"12"` -> `"12"`
    """
    if not value:
        return ""
    result = value.lower().replace("ё", "е").strip()
    result = _SLASH_SPACES_RE.sub("/", result)
    result = _HOUSE_STRIP_RE.sub(" ", result)
    return " ".join(result.split())


def house_lookup_key(value: str | None) -> str:
    """Ключ для `houses.housenumber_norm` (быстрый префильтр, не истина).

    Повторяет `house_number_norm()` из ETL: нижний регистр, ё->е, пунктуация
    (включая `/` и `-`) удаляется. Поэтому `"55/6"` -> `"556"`, а `"1/2"` ->
    `"12"` — именно поэтому после SQL нужна строгая проверка.
    """
    normalized = normalize_house(value)
    if not normalized:
        return ""
    return _HOUSE_LOOKUP_RE.sub("", normalized.replace("ё", "е"))


def core_street_name(value: str | None) -> str:
    """Ядро названия без базового слова типа ("улица Абая" -> "абая")."""
    normalized = normalize(value or "")
    if not normalized:
        return ""
    tokens = normalized.split()
    core = [token for token in tokens if vocab.canonical_street_type(token) is None]
    return " ".join(core) if core else normalized


@dataclass(frozen=True, slots=True)
class GeocodeResult:
    """Результат геокодинга одной записи."""

    id: str
    lat: float | None = None
    lon: float | None = None
    precision: str | None = None
    source: str | None = None


@dataclass(slots=True)
class _Target:
    """Внутреннее представление запроса: id + нормализованные ключи."""

    city_id: int | None = None
    district_id: int | None = None
    province_id: int | None = None
    street_id: int | None = None
    entity_type: str | None = None
    street_core: str = ""
    street_norm: str = ""
    house: str | None = None


@dataclass(frozen=True, slots=True)
class _StreetRef:
    """Минимальная ссылка на улицу/жилмассив для фоллбэка."""

    id: int
    entity_type: str | None = None
    city_id: int | None = None
    district_id: int | None = None
    province_id: int | None = None


@dataclass(frozen=True, slots=True)
class _HouseMatch:
    lat: float
    lon: float
    precision: str


def _street_names(entity: StreetEntity) -> set[str]:
    """Варианты названия с учётом казахских букв и типа в имени.

    В БД есть дубли вида «Аксай-3А» и «Аксай-3А микрорайон». Для геокодера
    они должны быть одним названием, иначе дом ищется только у первого
    кандидата, а второй (где может лежать нужный house) теряется.
    """
    names: set[str] = set()
    for value in (entity.name_norm, entity.name_core_norm, *entity.names):
        normalized = normalize(value or "")
        if not normalized:
            continue
        names.add(fold_kazakh(normalized))
        core = core_street_name(normalized)
        if core:
            names.add(fold_kazakh(core))
    return names


def _name_matches(entity: StreetEntity, target: _Target) -> bool:
    targets = {
        fold_kazakh(target.street_core),
        fold_kazakh(target.street_norm),
    }
    targets.discard("")
    return bool(targets & _street_names(entity))


def _rank_street(entity: StreetEntity, target: _Target) -> tuple[int, int, int, int]:
    """Чем меньше, тем лучше: точное имя важнее алиаса, улица — жилмассива."""
    full = fold_kazakh(target.street_norm)
    core = fold_kazakh(target.street_core)
    primary = {
        fold_kazakh(normalize(value))
        for value in (entity.name_norm, entity.name_core_norm)
        if value
    }
    if full in primary or core in primary:
        name_rank = 0
    elif full and full in _street_names(entity):
        name_rank = 1
    elif core and core in _street_names(entity):
        name_rank = 2
    else:
        name_rank = 3
    type_rank = 0
    if target.entity_type is not None and entity.entity_type != target.entity_type:
        type_rank = 1
    return (name_rank, type_rank, int(entity.city_id is None), entity.id)


class GeocoderService:
    """Forward-геокодинг: дом -> улица -> район -> город -> область."""

    def __init__(
        self,
        repository_provider: Callable[[], AddressRepository | NullAddressRepository],
        parser: AddressParserService,
        *,
        workers: int = 4,
    ) -> None:
        self._repository_provider = repository_provider
        self._parser = parser
        self._workers = max(1, workers)
        self._lock = threading.Lock()
        self._cache_repo: object | None = None
        self._centroids: dict[tuple[str, int], Centroid | None] = {}
        self._streets: dict[tuple[int | None, str, str], tuple[StreetEntity, ...]] = {}

    # -- публичный API ----------------------------------------------------- #

    def geocode(
        self,
        record_id: str,
        *,
        raw_address: str | None = None,
        city: str | None = None,
        street_name: str | None = None,
        house: str | None = None,
    ) -> GeocodeResult:
        """Одна запись. Ошибки БД/парсера не роняют сервис — отдаём null."""
        try:
            repository = self._repository_provider()
            if not repository.db_loaded or not repository.has_houses:
                return GeocodeResult(id=record_id)
            target = self._resolve_target(
                repository,
                record_id,
                raw_address=raw_address,
                city=city,
                street_name=street_name,
                house=house,
            )
            return self._locate(repository, record_id, target)
        except Exception:  # noqa: BLE001 - геокодинг не должен ронять API
            logger.exception("Геокодинг записи %s не удался", record_id)
            return GeocodeResult(id=record_id)

    def geocode_batch(
        self,
        records: Iterable[tuple[str, str | None, str | None, str | None, str | None]],
    ) -> list[GeocodeResult]:
        """Пакетная обработка с сохранением порядка записей."""
        items = list(records)
        if not items:
            return []
        if len(items) < 64 or self._workers == 1:
            return [self._geocode_tuple(item) for item in items]
        with ThreadPoolExecutor(
            max_workers=self._workers, thread_name_prefix="geocoder"
        ) as pool:
            return list(pool.map(self._geocode_tuple, items))

    def _geocode_tuple(
        self, item: tuple[str, str | None, str | None, str | None, str | None]
    ) -> GeocodeResult:
        record_id, raw_address, city, street_name, house = item
        return self.geocode(
            record_id,
            raw_address=raw_address,
            city=city,
            street_name=street_name,
            house=house,
        )

    # -- разбор входа ------------------------------------------------------ #

    def _resolve_target(
        self,
        repository: AddressRepository | NullAddressRepository,
        record_id: str,
        *,
        raw_address: str | None,
        city: str | None,
        street_name: str | None,
        house: str | None,
    ) -> _Target:
        raw = (raw_address or "").strip()
        if raw:
            parsed = self._parser.parse(record_id, raw)
            name = parsed.street_name or parsed.living_area or ""
            target = _Target(
                city_id=parsed.city_id,
                district_id=parsed.district_id,
                province_id=parsed.province_id,
                street_id=parsed.street_id,
                entity_type=parsed.street_entity_type,
                street_core=core_street_name(name),
                street_norm=normalize(name),
                house=parsed.house,
            )
            self._fill_ids_from_names(
                repository, target, parsed.city, parsed.district, parsed.province
            )
            return target

        city_norm = normalize(city or "")
        index = repository.classifiers()
        city_item = index.city_by_norm.get(city_norm) if city_norm else None
        target = _Target(
            city_id=city_item.id if city_item is not None else None,
            province_id=city_item.parent_id if city_item is not None else None,
            street_core=core_street_name(street_name),
            street_norm=normalize(street_name or ""),
            house=house,
        )
        if target.street_core:
            ref = self._resolve_one(repository, target)
            if ref is not None:
                self._apply_ref(target, ref)
        return target

    @staticmethod
    def _fill_ids_from_names(
        repository: AddressRepository | NullAddressRepository,
        target: _Target,
        city: str | None,
        district: str | None,
        province: str | None,
    ) -> None:
        index = repository.classifiers()
        if target.city_id is None and city:
            item = index.city_by_norm.get(normalize(city))
            if item is not None:
                target.city_id = item.id
                if target.province_id is None:
                    target.province_id = item.parent_id
        if target.province_id is None and province:
            province_norm = normalize(province)
            for item in index.provinces:
                if item.norm == province_norm:
                    target.province_id = item.id
                    break
        if target.district_id is None and district:
            district_norm = normalize(district)
            for item in index.districts:
                if item.norm != district_norm:
                    continue
                # Районы разных городов могут называться одинаково.
                if target.city_id is None or item.parent_id == target.city_id:
                    target.district_id = item.id
                    break

    # -- выбор улицы ------------------------------------------------------- #

    def _resolve_one(
        self,
        repository: AddressRepository | NullAddressRepository,
        target: _Target,
    ) -> _StreetRef | None:
        refs = self._candidate_refs(repository, target)
        if not refs:
            return None
        return refs[0]

    def _candidate_refs(
        self,
        repository: AddressRepository | NullAddressRepository,
        target: _Target,
    ) -> list[_StreetRef]:
        entities = self._resolved_entities(repository, target)
        matched = [entity for entity in entities if _name_matches(entity, target)]
        if not matched:
            return []
        if target.city_id is None:
            linked = [entity for entity in matched if entity.city_id is not None]
            # Улица без города не даёт права угадывать регион: "Атлантида"
            # не должна превращаться в дом на одноимённой улице в другом
            # городе (ТЗ, п.8).
            if not linked:
                return []
            city_ids = {entity.city_id for entity in linked}
            if len(city_ids) > 1:
                return []
            matched = linked
        matched.sort(key=lambda entity: _rank_street(entity, target))
        return [
            _StreetRef(
                id=entity.id,
                entity_type=entity.entity_type,
                city_id=entity.city_id,
                district_id=entity.district_id,
                province_id=entity.province_id,
            )
            for entity in matched
        ]

    def _resolved_entities(
        self,
        repository: AddressRepository | NullAddressRepository,
        target: _Target,
    ) -> tuple[StreetEntity, ...]:
        if not target.street_core:
            return ()
        key = (target.city_id, target.street_core, target.street_norm)
        with self._lock:
            self._ensure_cache(repository)
            cached = self._streets.get(key)
        if cached is not None:
            return cached
        resolved = tuple(
            repository.resolve_streets(
                target.street_core,
                target.street_norm,
                city_id=target.city_id,
            )
        )
        with self._lock:
            if repository is self._cache_repo:
                if len(self._streets) > 50_000:
                    self._streets.clear()
                self._streets[key] = resolved
        return resolved

    @staticmethod
    def _apply_ref(target: _Target, ref: _StreetRef) -> None:
        target.street_id = ref.id
        target.entity_type = ref.entity_type
        if target.city_id is None:
            target.city_id = ref.city_id
        if target.district_id is None:
            target.district_id = ref.district_id
        if target.province_id is None:
            target.province_id = ref.province_id

    # -- поиск точки ------------------------------------------------------- #

    def _locate(
        self,
        repository: AddressRepository | NullAddressRepository,
        record_id: str,
        target: _Target,
    ) -> GeocodeResult:
        if target.house:
            house = self._house_match(repository, target)
            if house is not None:
                return GeocodeResult(
                    id=record_id,
                    lat=house.lat,
                    lon=house.lon,
                    precision=house.precision,
                    source="osm",
                )

        refs: list[_StreetRef] = []
        if target.street_id is not None:
            refs.append(
                _StreetRef(
                    id=target.street_id,
                    entity_type=target.entity_type,
                    city_id=target.city_id,
                    district_id=target.district_id,
                    province_id=target.province_id,
                )
            )
        for ref in self._candidate_refs(repository, target):
            if all(ref.id != existing.id for existing in refs):
                refs.append(ref)

        for ref in refs:
            centroid = repository.street_centroid(ref.id)
            if centroid is None and ref.entity_type == "living_area":
                # Агрегат по place_id дорогой (нет отдельного индекса),
                # поэтому живёт в том же кэше, что и город/район/область.
                centroid = self._centroid(repository, "place", ref.id)
            if centroid is not None and centroid.count > 0:
                precision = "living_area" if ref.entity_type == "living_area" else "street"
                return GeocodeResult(
                    id=record_id,
                    lat=centroid.lat,
                    lon=centroid.lon,
                    precision=precision,
                    source="osm",
                )

        for level, entity_id in (
            ("district", target.district_id),
            ("city", target.city_id),
            ("province", target.province_id),
        ):
            centroid = self._centroid(repository, level, entity_id)
            if centroid is not None and centroid.count > 0:
                return GeocodeResult(
                    id=record_id,
                    lat=centroid.lat,
                    lon=centroid.lon,
                    precision=level,
                    source="osm",
                )
        return GeocodeResult(id=record_id)

    def _house_match(
        self,
        repository: AddressRepository | NullAddressRepository,
        target: _Target,
    ) -> _HouseMatch | None:
        strict = normalize_house(target.house)
        key = house_lookup_key(target.house)
        if not strict or not key:
            return None

        seen: set[int] = set()
        # 1. street_id из парсера — приоритетный источник (ТЗ, п.8).
        if target.street_id is not None:
            seen.add(target.street_id)
            found = self._points_to_match(
                repository, target.street_id, target.entity_type, key, strict
            )
            if found is not None:
                return found

        # 2. Дубли/алиасы: тот же номер дома может лежать на другой улице
        #    этого же города (например, парсер выбрал «Ахметовой» по алиасу
        #    «Абая», а дома привязаны к «улице Абая»).
        for ref in self._candidate_refs(repository, target):
            if ref.id in seen:
                continue
            seen.add(ref.id)
            found = self._points_to_match(
                repository, ref.id, ref.entity_type, key, strict
            )
            if found is not None:
                self._apply_ref(target, ref)
                return found
        return None

    @staticmethod
    def _points_to_match(
        repository: AddressRepository | NullAddressRepository,
        entity_id: int,
        entity_type: str | None,
        lookup_key: str,
        strict: str,
    ) -> _HouseMatch | None:
        points: list[HousePoint] = repository.find_houses(entity_id, lookup_key)
        if not points and entity_type == "living_area":
            points = repository.find_houses_by_place(entity_id, lookup_key)
        matched = [
            point for point in points if normalize_house(point.housenumber) == strict
        ]
        if not matched:
            return None
        if len(matched) == 1:
            point = matched[0]
            return _HouseMatch(point.lat, point.lon, "house")
        count = len(matched)
        return _HouseMatch(
            lat=sum(point.lat for point in matched) / count,
            lon=sum(point.lon for point in matched) / count,
            precision="house_approx",
        )

    # -- кэш центроидов ---------------------------------------------------- #

    def _ensure_cache(self, repository: object) -> None:
        """Сбросить кэш при hot-reload БД (новый объект репозитория)."""
        if self._cache_repo is not repository:
            self._cache_repo = repository
            self._centroids.clear()
            self._streets.clear()

    def _centroid(
        self,
        repository: AddressRepository | NullAddressRepository,
        level: str,
        entity_id: int | None,
    ) -> Centroid | None:
        if entity_id is None:
            return None
        key = (level, entity_id)
        with self._lock:
            self._ensure_cache(repository)
            if key in self._centroids:
                return self._centroids[key]
        value = repository.centroid(level, entity_id)
        with self._lock:
            if repository is self._cache_repo:
                self._centroids[key] = value
        return value


__all__ = [
    "GeocodeResult",
    "GeocoderService",
    "PRECISIONS",
    "core_street_name",
    "house_lookup_key",
    "normalize_house",
]
