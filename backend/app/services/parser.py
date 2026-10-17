"""Ядро разбора адреса: предобработка -> tail parser -> FTS5 lookup -> обогащение.

Порядок строго по ТЗ:
    1. предобработка и разделение склеек;
    2. вырезание деталей (Tail Parser) с фиксацией спанов;
    3. запрос к БД (FTS5) по остатку строки;
    4. позиционный фоллбэк для «голых» чисел.

Значения `street_name`, `living_area`, `landmark`, `house`, `apartment`,
`entrance`, `floor` — всегда слайсы исходной строки: опечатки и падежи
пользователя не исправляются.
"""

from __future__ import annotations

import bisect
import difflib
import logging
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache

from app.core import vocabulary as vocab
from app.core.tail_parser import GluedHint, TailSpans, parse_tail
from app.core.text import (
    Token,
    WorkText,
    fold_kazakh,
    iter_tokens,
    normalize,
    slice_original,
    tokenize,
)
from app.repositories.repository import (
    CLASSIFIER_STOP,
    REPUBLICAN_CITIES,
    AddressRepository,
    Classifier,
    ClassifierIndex,
    NullAddressRepository,
    StreetEntity,
    classifier_keys,
)

logger = logging.getLogger(__name__)

# Типы, которые по своей природе являются жилым массивом (fallback без БД).
LIVING_AREA_TYPES = frozenset({"микрорайон", "квартал"})

# Минимальная похожесть названия, при которой сущность из FTS считается
# найденной. Слабее — Regex-fallback без обогащения (ТЗ: без случайных регионов).
MIN_ENTITY_SIMILARITY = 0.8
# Порог для справочников: стемминг сводит "Абая" -> "аба", поэтому одного
# совпадения ключа мало — проверяем ещё и похожесть самих названий.
MIN_CLASSIFIER_SIMILARITY = 0.8

_KIND_PRIORITY = {"province": 0, "city": 1, "district": 2}
_HINT_TO_KIND = {
    "район": "district", "ауданы": "district", "аудан": "district", "р-н": "district",
    "рн": "district", "область": "province", "облысы": "province", "обл": "province",
    "город": "city", "қаласы": "city", "каласы": "city", "г": "city",
}


# --------------------------------------------------------------------------- #
# Результат
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ParsedAddress:
    """Одна размеченная запись."""

    id: str
    raw_address: str
    province: str | None = None
    city: str | None = None
    district: str | None = None
    street_type: str | None = None
    street_name: str | None = None
    living_area: str | None = None
    landmark: str | None = None
    house: str | None = None
    apartment: str | None = None
    entrance: str | None = None
    floor: str | None = None
    confidence: float = 0.5
    # Внутренние идентификаторы для геокодинга. В API (`to_dict`) не попадают.
    street_id: int | None = None
    city_id: int | None = None
    district_id: int | None = None
    province_id: int | None = None
    street_entity_type: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "raw_address": self.raw_address,
            "province": self.province,
            "city": self.city,
            "district": self.district,
            "street_type": self.street_type,
            "street_name": self.street_name,
            "living_area": self.living_area,
            "landmark": self.landmark,
            "house": self.house,
            "apartment": self.apartment,
            "entrance": self.entrance,
            "floor": self.floor,
            "confidence": self.confidence,
        }


# Порядок полей в кэшируемом кортеже.
_FIELDS = (
    "province", "city", "district", "street_type", "street_name", "living_area",
    "landmark", "house", "apartment", "entrance", "floor", "confidence",
)
_EMPTY_FIELDS: tuple[object, ...] = (None,) * (len(_FIELDS) - 1) + (0.0,)


# --------------------------------------------------------------------------- #
# Вспомогательные структуры
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Classification:
    kind: str
    item: Classifier
    start: int  # индекс первого токена (включительно)
    end: int    # индекс последнего токена + 1


@dataclass(slots=True)
class _ParsedFields:
    """Поля ответа + внутренние id для геокодера."""

    values: tuple[object, ...]
    street_id: int | None = None
    city_id: int | None = None
    district_id: int | None = None
    province_id: int | None = None
    street_entity_type: str | None = None

    # Обратная совместимость с прежним tuple-результатом `_parse_fields`.
    def __iter__(self) -> Iterator[object]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def __getitem__(self, index: int) -> object:
        return self.values[index]


@dataclass(slots=True)
class _CoreResult:
    province: str | None = None
    city: str | None = None
    district: str | None = None
    street_type: str | None = None
    street_name: str | None = None
    living_area: str | None = None
    confidence: float = 0.5
    matched: bool = False
    # Внутренние id (в ответ API не выводятся, нужны только геокодеру).
    street_id: int | None = None
    city_id: int | None = None
    district_id: int | None = None
    province_id: int | None = None
    street_entity_type: str | None = None


# --------------------------------------------------------------------------- #
# Сопоставление классификаторов
# --------------------------------------------------------------------------- #

_PSEUDO_DISTRICT_RE = re.compile(r"(?:^|\s)г\s*а$")


def _is_pseudo_district(name: str) -> bool:
    """"Актобе Г.А" / "Темиртау Г.А" — городская администрация, не район."""
    return bool(_PSEUDO_DISTRICT_RE.search(normalize(name)))


def _has_explicit_district_hint(tokens: Sequence[Token], item: _Classification) -> bool:
    """Есть ли рядом слово "район"/"аудан" — тогда это точно район."""
    if _hint_kind(tokens, item.end) == "district":
        return True
    if item.start > 0 and _HINT_TO_KIND.get(tokens[item.start - 1].norm) == "district":
        return True
    # Классификатор может захватить служебное слово в свой спан
    # ("район Туран" -> start=0, end=2).
    return any(
        _HINT_TO_KIND.get(tokens[index].norm) == "district"
        for index in range(item.start, item.end)
    )


def _district_shadowed_by_street(
    repository: AddressRepository | NullAddressRepository,
    tokens: Sequence[Token],
    item: _Classification,
) -> bool:
    """Одно и то же слово есть и районом, и улицей/проспектом.

    Пример: "Туран" — район в Шымкенте и проспект/улица в Астане/Шымкенте.
    Без явного слова "район" отдаём приоритет названию улицы, чтобы
    street_name не остался пустым.
    """
    if _has_explicit_district_hint(tokens, item):
        return False
    target = fold_kazakh(normalize(item.item.name))
    if not target:
        return False
    for entity in repository.search_streets([item.item.norm], limit=16):
        if entity.entity_type != "street":
            continue
        if any(fold_kazakh(name) == target for name in _target_names(entity)):
            return True
    return False


def _hint_kind(tokens: Sequence[Token], end: int) -> str | None:
    """Служебное слово сразу после названия уточняет тип классификатора."""
    if end < len(tokens):
        return _HINT_TO_KIND.get(tokens[end].norm)
    return None


def _classifier_is_similar(window_norm: str, stripped_window: str, item: Classifier) -> bool:
    """Совпадение по стеммированному ключу подтверждаем похожестью названий.

    Без этого "Абая" (стем "аба") притягивает провинцию "Абай область".
    """
    stripped_name = " ".join(t for t in tokenize(item.norm) if t not in CLASSIFIER_STOP)
    score = _similarity(window_norm, item.norm)
    if stripped_name and stripped_window:
        score = max(score, _similarity(stripped_window, stripped_name))
    return score >= MIN_CLASSIFIER_SIMILARITY


def _match_classifiers(tokens: Sequence[Token], index: ClassifierIndex) -> list[_Classification]:
    """Ищем области/города/районы в тексте адреса (до 4 слов в названии)."""
    if index.is_empty:
        return []

    tables = (
        ("province", index.province_keys),
        ("city", index.city_keys),
        ("district", index.district_keys),
    )
    candidates: list[tuple[tuple[int, int, int, int], _Classification]] = []

    for start in range(len(tokens)):
        for size in (4, 3, 2, 1):
            window = tokens[start:start + size]
            if len(window) < size:
                continue
            window_norm = " ".join(t.norm for t in window)
            stripped_window = " ".join(
                t.norm for t in window if t.norm not in CLASSIFIER_STOP
            )
            for key in classifier_keys(window_norm):
                if not key:
                    continue
                for kind, table in tables:
                    for item in table.get(key, ()):
                        if not _classifier_is_similar(window_norm, stripped_window, item):
                            continue
                        # "Мкр Аксай 3а": Аксай — часть названия жилого
                        # массива, а не город. Тип перед словом однозначно
                        # говорит, что это топоним, а не классификатор.
                        if kind == "city" and start > 0 and vocab.STREET_TYPE_RE.fullmatch(
                            tokens[start - 1].raw
                        ):
                            continue
                        hint = _hint_kind(tokens, start + size)
                        score = (
                            1 if hint == kind else 0,
                            size,
                            -_KIND_PRIORITY[kind],
                            -start,
                        )
                        candidates.append(
                            (score, _Classification(kind, item, start, start + size))
                        )

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    chosen: list[_Classification] = []
    spans: dict[str, set[int]] = {}
    for _score, candidate in candidates:
        if candidate.kind in spans:
            continue
        # Служебное слово рядом ("область", "район", "город") однозначно
        # задаёт тип: "Алматинская область" — не район "Алматинский".
        hint = _hint_kind(tokens, candidate.end)
        if hint is not None and hint != candidate.kind:
            continue
        positions = set(range(candidate.start, candidate.end))
        # Один и тот же отрезок может быть и городом, и областью ("Алматы"),
        # но разные типы не должны пересекаться частично.
        conflict = any(
            positions & other and positions != other for other in spans.values()
        )
        if conflict:
            continue
        chosen.append(candidate)
        spans[candidate.kind] = positions
        if len(spans) == 3:
            break
    return chosen


# --------------------------------------------------------------------------- #
# Похожесть и выбор сущности
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=65_536)
def _tokens_alike(left: str, right: str) -> bool:
    """Токены считаются совпавшими с учётом опечаток и словоформ."""
    if left == right:
        return True
    if len(left) >= 3 and len(right) >= 3 and (left.startswith(right) or right.startswith(left)):
        return True
    return difflib.SequenceMatcher(None, left, right, autojunk=False).ratio() >= 0.75


@lru_cache(maxsize=65_536)
def _similarity(query: str, name: str) -> float:
    """Похожесть названия из БД и отрезка адреса.

    Символьная похожесть со штрафом за непокрытые токены названия: лишние
    слова в названии ("66-й квартал" против "66 квартал") заметно снижают
    оценку, а опечатка внутри токена ("казибек" / "казыбек") — почти нет.
    Казахские буквы перед сравнением сводятся к русским (ә->а, қ->к, ...):
    "Мәңгілік Ел" и "Мангилик ел" — одно название.
    """
    if not query or not name:
        return 0.0
    query = fold_kazakh(query)
    name = fold_kazakh(name)
    if query == name:
        return 1.0
    char_ratio = difflib.SequenceMatcher(None, query, name, autojunk=False).ratio()
    name_tokens = name.split()
    query_tokens = query.split()
    if name_tokens and query_tokens:
        matched = sum(
            1 for name_token in name_tokens
            if any(_tokens_alike(name_token, query_token) for query_token in query_tokens)
        )
        coverage = matched / len(name_tokens)
    else:
        coverage = 0.0
    return char_ratio * (0.5 + 0.5 * coverage)


@dataclass(slots=True)
class _EntityMatch:
    """Найденная в БД сущность и точный спан её названия в адресе."""

    entity: StreetEntity
    start: int
    end: int
    score: float


_CITY_CONTEXT_WORDS = frozenset(
    {
        "город",
        "г",
        "қаласы",
        "каласы",
        "облысы",
        "область",
        "ауданы",
        "район",
        "республикасы",
        "республика",
    }
)


def _city_context_spans(tokens: Sequence[Token]) -> set[tuple[int, int]]:
    """Спаны соседних с городом/областью слов (для двуязычных склеек).

    Пример: "Актобе/Ақтөбе қаласы" — «Ақтөбе» не должно стать улицей.
    """
    spans: set[tuple[int, int]] = set()
    for index, token in enumerate(tokens):
        if token.norm not in _CITY_CONTEXT_WORDS:
            continue
        if index > 0:
            prev = tokens[index - 1]
            spans.add((prev.start, prev.end))
        if index + 1 < len(tokens):
            nxt = tokens[index + 1]
            spans.add((nxt.start, nxt.end))
    return spans


def _has_living_type_near(tokens: Sequence[Token], match: _EntityMatch) -> bool:
    """Есть ли рядом с найденным названием слово «мкр/микрорайон/жк».

    Нужно, чтобы не принять за жилмассив часть двуязычной склейки города
    («Актобе/Ақтөбе қаласы»): у настоящего микрорайона тип стоит рядом.
    """
    before = [token for token in tokens if token.end <= match.start]
    after = [token for token in tokens if token.start >= match.end]
    inside = [
        token
        for token in tokens
        if token.start >= match.start and token.end <= match.end
    ]
    around = inside + before[-1:] + after[:1]
    return any(
        vocab.canonical_street_type(token.raw) in LIVING_AREA_TYPES
        for token in around
    )


def _slice_without_type(
    work: WorkText, tokens: Sequence[Token], start: int, end: int
) -> str:
    """Слайс названия без базового слова типа.

    Для микрорайона/квартала ТЗ требует `street_name` без типа:
    "12 микрорайон" -> "12", "Аксай 3а" -> "Аксай 3а".
    """
    selected = [
        token
        for token in tokens
        if token.start >= start
        and token.end <= end
        and vocab.canonical_street_type(token.raw) is None
    ]
    if not selected:
        return work.original_slice(start, end)
    return work.original_slice(selected[0].start, selected[-1].end)


def _strip_type_tokens(value: str) -> str:
    """Убрать базовые слова типа из названия ("улица Абая" -> "абая").

    `name_core_norm` в боевой базе местами всё ещё содержит тип
    ("улица наримановская", "наримановская улица"), поэтому сравниваем ещё
    и ядро, полученное срезанием известных типов из полного имени.
    """
    tokens = value.split()
    core = [token for token in tokens if vocab.canonical_street_type(token) is None]
    return " ".join(core) if core else value


@lru_cache(maxsize=32_768)
def _target_names(candidate: StreetEntity) -> tuple[str, ...]:
    """Все варианты названия для сопоставления: полные и ядра."""
    targets: list[str] = []
    for name in candidate.names:
        target = normalize(name)
        if target:
            targets.append(target)
    if candidate.name_core_norm:
        core = normalize(candidate.name_core_norm)
        if core:
            targets.append(core)
    # Ядро, выведенное из полного имени: страхует от "грязного"
    # name_core_norm, в котором остался тип ("улица ...").
    for target in list(targets):
        stripped = _strip_type_tokens(target)
        if stripped and stripped != target:
            targets.append(stripped)
    return tuple(dict.fromkeys(targets))


def _best_match_for_entity(
    name_tokens: Sequence[Token], candidate: StreetEntity
) -> _EntityMatch | None:
    """Лучшее окно адреса для одного кандидата (без порога)."""
    best: _EntityMatch | None = None
    for target in _target_names(candidate):
        target_len = len(target.split())
        sizes = {target_len, max(1, target_len - 1), target_len + 1, target_len + 2}
        for size in sizes:
            if size < 1 or size > len(name_tokens):
                continue
            for start in range(len(name_tokens) - size + 1):
                window_text = " ".join(t.norm for t in name_tokens[start:start + size])
                score = _similarity(window_text, target)
                start_pos = name_tokens[start].start
                end_pos = name_tokens[start + size - 1].end
                # При равной похожести выигрывает более длинный отрезок:
                # куцый name_norm ("12") не должен отрезать "микрорайон".
                longer = best is not None and (end_pos - start_pos) > (best.end - best.start)
                if best is None or score > best.score + 1e-9 or (
                    abs(score - best.score) <= 1e-9 and longer
                ):
                    best = _EntityMatch(
                        entity=candidate,
                        start=start_pos,
                        end=end_pos,
                        score=score,
                    )
    return best


def _exact_match(
    name_tokens: Sequence[Token], candidates: Sequence[StreetEntity]
) -> _EntityMatch | None:
    """Быстрый путь: ядро/полное имя точно совпало с названием адреса.

    Точное совпадение (score=1.0) не требует перебора окон и difflib —
    это заметная часть боевых адресов и главный резерв скорости пакета.
    """
    query = fold_kazakh(" ".join(token.norm for token in name_tokens))
    best: _EntityMatch | None = None
    start = name_tokens[0].start
    end = name_tokens[-1].end
    for candidate in candidates:
        matched = any(fold_kazakh(target) == query for target in _target_names(candidate))
        if not matched:
            continue
        if best is None:
            best = _EntityMatch(candidate, start, end, 1.0)
            continue
        # При равном score=1.0 предпочитаем сущность с типом улицы и
        # городом/районом: в БД бывают дубли без street_type.
        if (
            int(candidate.street_type is not None),
            int(candidate.city_id is not None),
            int(candidate.district_id is not None),
            int(candidate.province_id is not None),
        ) > (
            int(best.entity.street_type is not None),
            int(best.entity.city_id is not None),
            int(best.entity.district_id is not None),
            int(best.entity.province_id is not None),
        ):
            best = _EntityMatch(candidate, start, end, 1.0)
    return best


def _match_entity(
    name_tokens: Sequence[Token], candidates: Sequence[StreetEntity]
) -> _EntityMatch | None:
    """Выбрать лучшего кандидата из FTS и локализовать его название в адресе.

    Отрезок адреса сравнивается с каждым вариантом названия (name_norm,
    name_core_norm, ядро без типа, нормализованное официальное название,
    старые названия) скользящим окном: так слайс не захватывает посторонние
    слова, а лишние токены адреса не мешают совпадению.

    Сущность принимается только при `score >= MIN_ENTITY_SIMILARITY`; иначе
    возвращается None — разбор уходит в Regex-fallback без обогащения
    регионами/городом (чтобы не приписать случайную область).
    """
    if not name_tokens or not candidates:
        return None
    exact = _exact_match(name_tokens, candidates)
    if exact is not None:
        return exact

    def rank(match: _EntityMatch) -> tuple[float, int, int, int, int, int]:
        entity = match.entity
        return (
            match.score,
            match.end - match.start,
            int(entity.street_type is not None),
            int(entity.city_id is not None),
            int(entity.district_id is not None),
            int(entity.province_id is not None),
        )

    best: _EntityMatch | None = None
    for candidate in candidates:
        found = _best_match_for_entity(name_tokens, candidate)
        if found is None:
            continue
        if best is None or rank(found) > rank(best):
            best = found
    if best is None or best.score < MIN_ENTITY_SIMILARITY:
        return None
    return best


def _has_unlinked_leading_candidate(
    name_tokens: Sequence[Token],
    candidates: Sequence[StreetEntity],
    best: _EntityMatch,
) -> bool:
    """Ведущее название есть в БД, но не слинковано с городом.

    Пример: "Нурсити 2 микрорайон ..." — «Нурсити» находится как street
    с city_id=NULL, но парсер выбирает более длинный "2 микрорайон"
    из случайного города. В таком случае город из БД использовать нельзя.
    """
    # Сам выбранный объект не слинкован — город точно не подставляем.
    if best.entity.city_id is None:
        return True
    if not name_tokens or len(name_tokens[0].norm) < 2:
        return False

    first = name_tokens[0]
    first_norm = fold_kazakh(first.norm)
    for candidate in candidates:
        if candidate.city_id is not None or candidate.id == best.entity.id:
            continue
        for target in _target_names(candidate):
            folded = fold_kazakh(target)
            if folded == first_norm or folded.startswith(first_norm + " "):
                # Ведущее слово не входит в выбранный спан — совпадение
                # относится к другому, непривязанному названию.
                if best.start > first.start:
                    return True
    return False


def _is_ambiguous_match(
    name_tokens: Sequence[Token],
    candidates: Sequence[StreetEntity],
    best: _EntityMatch,
) -> bool:
    """Одно и то же название встречается в нескольких городах почти одинаково.

    Город `None` не считается отдельным городом: такие записи не дают
    достаточного сигнала для выбора и не должны блокировать уникальный
    городской вариант.
    """
    best_length = best.end - best.start
    city_ids: set[int] = set()
    for candidate in candidates:
        found = _best_match_for_entity(name_tokens, candidate)
        if found is None or found.score < best.score - 0.05:
            continue
        # Совпадение только с коротким ядром ("3а" вместо "Аксай 3а")
        # не делает название неоднозначным: это менее полный вариант того же
        # адреса, а не другой город.
        if (found.end - found.start) < best_length:
            continue
        if candidate.city_id is not None:
            city_ids.add(candidate.city_id)
    return len(city_ids) > 1


# --------------------------------------------------------------------------- #
# Сервис
# --------------------------------------------------------------------------- #


class AddressParserService:
    """Разбор адресов с параллельной обработкой пакета.

    Полные строки адресов намеренно НЕ кэшируются как готовые ответы:
    ТЗ запрещает возврат ответа по точному совпадению всей входной строки.
    LRU используется только для промежуточных вычислений (похожесть,
    варианты названий, ключи классификаторов).
    """

    def __init__(
        self,
        repository_provider: Callable[[], AddressRepository | NullAddressRepository],
        *,
        workers: int = 4,
    ) -> None:
        self._repository_provider = repository_provider
        self._workers = max(1, workers)

    # -- публичный API ----------------------------------------------------- #

    @property
    def repository(self) -> AddressRepository | NullAddressRepository:
        return self._repository_provider()

    def parse(self, record_id: str, raw_address: str) -> ParsedAddress:
        fields = self._parse_fields(raw_address or "")
        return ParsedAddress(
            id=record_id,
            raw_address=raw_address or "",
            **dict(zip(_FIELDS, fields.values, strict=True)),
            street_id=fields.street_id,
            city_id=fields.city_id,
            district_id=fields.district_id,
            province_id=fields.province_id,
            street_entity_type=fields.street_entity_type,
        )

    def parse_batch(self, records: Iterable[tuple[str, str]]) -> list[ParsedAddress]:
        """Пакетная обработка. Порядок записей сохраняется."""
        items = list(records)
        if not items:
            return []
        if len(items) < 64 or self._workers == 1:
            return [self.parse(record_id, raw) for record_id, raw in items]
        with ThreadPoolExecutor(max_workers=self._workers, thread_name_prefix="parser") as pool:
            return list(pool.map(lambda item: self.parse(*item), items))

    # -- основной конвейер ------------------------------------------------- #

    def _parse_fields(self, raw_address: str) -> _ParsedFields:
        if not raw_address or not raw_address.strip():
            return _ParsedFields(values=_EMPTY_FIELDS)

        work = WorkText(raw_address)
        hints = self._preprocess(work)

        tail: TailSpans = parse_tail(work, hints)

        repository = self._repository_provider()
        core = self._parse_core(work, repository, tail.protected, tail)

        return _ParsedFields(
            values=(
                core.province,
                core.city,
                core.district,
                core.street_type,
                core.street_name,
                core.living_area,
                tail.landmark,
                tail.house,
                tail.apartment,
                tail.entrance,
                tail.floor,
                core.confidence,
            ),
            street_id=core.street_id,
            city_id=core.city_id,
            district_id=core.district_id,
            province_id=core.province_id,
            street_entity_type=core.street_entity_type,
        )

    # -- шаг 1: предобработка --------------------------------------------- #

    @staticmethod
    def _preprocess(work: WorkText) -> list[GluedHint]:
        """Разлепление слитных написаний вставкой пробелов.

        Смещения исходной строки не ломаются (вставка пробела — единственная
        разрешённая модификация), а из слитных пар сразу извлекаются подсказки:
        в "12дом", "4подьезд", "flat191" число однозначно принадлежит
        ключевому слову.
        """
        raw = work.text
        insertions: list[int] = []
        # (поле, начало числа, конец числа, начало пары, конец пары)
        raw_hints: list[tuple[str | None, int, int, int, int]] = []

        for match in vocab.GLUED_NUMBER_KEYWORD_RE.finditer(raw):
            insertions.append(match.start("kw"))
            raw_hints.append(
                (
                    vocab.field_for_keyword(match.group("kw")),
                    match.start("num"), match.end("num"),
                    match.start("num"), match.end("kw"),
                )
            )
        for match in vocab.GLUED_KEYWORD_DIGIT_RE.finditer(raw):
            insertions.append(match.start("num"))
            raw_hints.append(
                (
                    vocab.field_for_keyword(match.group("kw")),
                    match.start("num"), match.end("num"),
                    match.start("kw"), match.end("num"),
                )
            )
        insertions.extend(m.start() for m in vocab.GLUED_DOT_WORD_RE.finditer(raw))
        insertions.extend(m.start() for m in vocab.GLUED_SLASH_WORD_RE.finditer(raw))

        work.insert_spaces(insertions)

        if not raw_hints:
            return []
        ordered = sorted(set(insertions))
        hints: list[GluedHint] = []
        for field_name, num_start, num_end, pair_start, pair_end in raw_hints:
            if not field_name:
                continue
            # Перевод смещений исходной строки в координаты рабочей копии.
            start = pair_start + bisect.bisect_right(ordered, pair_start)
            end = pair_end + bisect.bisect_left(ordered, pair_end)
            hints.append(
                GluedHint(
                    field=field_name,
                    value=slice_original(raw, num_start, num_end),
                    start=start,
                    end=end,
                )
            )
        return hints

    # -- шаги 3-4: адресное ядро ------------------------------------------ #

    def _parse_core(
        self,
        work: WorkText,
        repository: AddressRepository | NullAddressRepository,
        protected: Sequence[tuple[int, int]] = (),
        tail: TailSpans | None = None,
    ) -> _CoreResult:
        tokens = list(iter_tokens(work.text))
        index = repository.classifiers()
        result = _CoreResult()

        # 1. Классификаторы: область / город / район.
        classifications = _match_classifiers(tokens, index)
        used: set[int] = set()
        city_norms = {
            normalize(item.item.name)
            for item in classifications
            if item.kind == "city"
        }
        filtered: list[_Classification] = []
        for item in classifications:
            if item.kind == "district":
                if (
                    normalize(item.item.name) in city_norms
                    or _is_pseudo_district(item.item.name)
                ):
                    # В Казахстане есть районы, названные как города ("Алматы",
                    # "Тараз"), и псевдорайоны "X Г.А". Это не район адреса.
                    used |= set(range(item.start, item.end))
                    continue
                if _district_shadowed_by_street(repository, tokens, item):
                    # "Туран" без слова "район" — это улица/проспект, а не
                    # район. Токен оставляем свободным для поиска в БД.
                    continue
            filtered.append(item)
        classifications = filtered
        for item in classifications:
            used |= set(range(item.start, item.end))
            setattr(result, item.kind, item.item.name)
            # id классификатора нужен геокодеру для фоллбэка на центроид.
            setattr(result, f"{item.kind}_id", item.item.id)

        # 2. Тип улицы/жилого массива — базовое слово.
        type_positions: dict[int, str] = {}
        for position, token in enumerate(tokens):
            if position in used:
                continue
            if vocab.STREET_TYPE_RE.fullmatch(token.raw):
                canonical = vocab.canonical_street_type(token.raw) or token.norm
                type_positions[position] = canonical
                if result.street_type is None:
                    result.street_type = canonical

        protected_positions = {
            position
            for position, token in enumerate(tokens)
            if any(token.start < end and start < token.end for start, end in protected)
        }

        # 3. Число после названия, но не в конце строки ("Сейфуллина 33 (Астана)"):
        #    за ним уже нет неразобранных слов — значит это дом.
        house_positions: set[int] = set()
        if tail is not None and tail.house is None:
            for position, token in enumerate(tokens):
                if position in used or position in protected_positions:
                    continue
                if not vocab.BARE_NUMBER_RE.fullmatch(token.raw):
                    continue
                if any(p not in used for p in range(position + 1, len(tokens))):
                    continue
                value = work.original_slice(token.start, token.end)
                if value:
                    tail.house = value
                    house_positions.add(position)
                    work.mask(token.start, token.end)
                    break

        # 4. Значимые токены остатка строки (кандидат в название).
        phrase: list[tuple[int, Token]] = []
        for position, token in enumerate(tokens):
            if position in used or position in house_positions:
                continue
            if position in type_positions:
                phrase.append((position, token))
                continue
            if token.norm in vocab.STOP_WORDS or token.norm in CLASSIFIER_STOP:
                continue
            phrase.append((position, token))

        if not phrase:
            result.confidence = self._confidence(repository, result, 0.0)
            return result

        # 5. Запрос в БД по остатку строки.
        #    Казахские токены НЕ отсекаются: "Мәңгілік Ел", "Қабанбай батыр"
        #    должны попадать в FTS как есть (иначе от названия остаётся "Ел").
        query_tokens = [
            token.norm
            for position, token in phrase
            if position not in type_positions or type_positions[position] in LIVING_AREA_TYPES
        ]
        name_tokens = [
            token
            for position, token in phrase
            if position not in type_positions or type_positions[position] in LIVING_AREA_TYPES
        ] or [token for _, token in phrase]
        name_span = (name_tokens[0].start, name_tokens[-1].end)

        city_in_text = any(c.kind == "city" for c in classifications)
        match: _EntityMatch | None = None
        candidates: list[StreetEntity] = []
        ratio = 0.0
        level = "none"
        preferred_type: str | None = None
        if result.street_type in LIVING_AREA_TYPES:
            preferred_type = "living_area"
        elif result.street_type is not None:
            preferred_type = "street"

        has_living_type = any(
            kind in LIVING_AREA_TYPES for kind in type_positions.values()
        )
        has_street_type = any(
            kind not in LIVING_AREA_TYPES for kind in type_positions.values()
        )

        # "Мкр/ЖК + отдельная улица": ищем обе сущности независимо.
        # Город должен быть в строке, иначе не обогащаем гео-поля.
        if city_in_text and query_tokens and has_living_type and has_street_type:
            living_match, _, _ = self._lookup_streets(
                repository,
                query_tokens,
                name_tokens,
                classifications,
                "living_area",
            )
            if (
                living_match is not None
                and living_match.entity.entity_type == "living_area"
                and _has_living_type_near(tokens, living_match)
            ):
                street_match = self._find_separate_entity(
                    repository, phrase, living_match, classifications, "street", tokens
                )
                if street_match is not None:
                    self._apply_entity(
                        result,
                        street_match.entity,
                        index,
                        work.original_slice(street_match.start, street_match.end),
                        trusted=True,
                    )
                    result.living_area = _slice_without_type(
                        work, tokens, living_match.start, living_match.end
                    )
                    result.matched = True
                    self._fill_province_from_city(result, index)
                    result.confidence = self._confidence(
                        repository, result, street_match.score
                    )
                    return result

        if query_tokens:
            match, level, candidates = self._lookup_streets(
                repository, query_tokens, name_tokens, classifications, preferred_type
            )
            if match is not None:
                ratio = match.score

        explicit_geo = bool(result.province or result.city or result.district)
        ambiguous = False
        weak = False
        trust_parents = True
        if match is not None:
            if city_in_text:
                # Город указан пользователем. Если в этом городе улицы нет,
                # имя всё равно может быть верным — не подмешиваем родителей
                # из чужого города, но и не штрафуем уверенность.
                trust_parents = level != "none"
            else:
                ambiguous = _is_ambiguous_match(name_tokens, candidates, match)
                unlinked = _has_unlinked_leading_candidate(name_tokens, candidates, match)
                trust_parents = not ambiguous and not unlinked
                # Без города в тексте неоднозначную улицу или название,
                # не привязанное к городу, не обогащаем регионами.
                weak = unlinked or (ambiguous and not explicit_geo)

        # 6. Заполнение названия и обогащение из БД.
        if match is not None:
            result.matched = True
            self._apply_entity(
                result,
                match.entity,
                index,
                work.original_slice(match.start, match.end),
                trusted=trust_parents,
            )
            if match.entity.entity_type == "street" and has_living_type:
                living_match = self._find_separate_entity(
                    repository, phrase, match, classifications, "living_area", tokens
                )
                if living_match is not None:
                    result.living_area = _slice_without_type(
                        work, tokens, living_match.start, living_match.end
                    )

            if match.entity.entity_type == "living_area":
                # Микрорайон/квартал как основной адресный элемент — это
                # street_name по ТЗ ("Мкр Аксай 3а" -> street_name "Аксай 3а",
                # living_area null). living_area заполняется только если
                # отдельно указана ещё и улица.
                living_slice = _slice_without_type(
                    work, tokens, match.start, match.end
                )
                street_match = (
                    self._find_separate_entity(
                        repository,
                        phrase,
                        match,
                        classifications,
                        "street",
                        tokens,
                    )
                    if has_street_type
                    else None
                )
                if street_match is not None:
                    result.living_area = living_slice
                    result.street_name = work.original_slice(
                        street_match.start, street_match.end
                    )
                    result.street_id = street_match.entity.id
                    result.street_entity_type = street_match.entity.entity_type
                    db_type = (
                        vocab.canonical_street_type(street_match.entity.street_type)
                        or street_match.entity.street_type
                    )
                    if db_type:
                        result.street_type = db_type
                    if result.city is None and street_match.entity.city_id is not None:
                        city = index.city_by_id.get(street_match.entity.city_id)
                        if city is not None:
                            result.city = city.name
                            result.city_id = city.id
                    if (
                        result.district is None
                        and street_match.entity.district_id is not None
                    ):
                        district = index.district_by_id.get(
                            street_match.entity.district_id
                        )
                        if district is not None:
                            result.district = district.name
                            result.district_id = district.id
                else:
                    result.street_name = living_slice
                    result.living_area = None
                    db_type = (
                        vocab.canonical_street_type(match.entity.street_type)
                        or match.entity.street_type
                    )
                    if db_type and trust_parents:
                        result.street_type = db_type
        else:
            name_slice = work.original_slice(*name_span)
            if result.street_type in LIVING_AREA_TYPES:
                result.street_name = _slice_without_type(
                    work, tokens, *name_span
                )
                result.living_area = None
            else:
                result.street_name = name_slice

        if weak:
            # Никаких гео-догадок: только явный текст адреса.
            result.province = result.city = result.district = None

        self._fill_province_from_city(result, index)
        result.confidence = self._confidence(repository, result, ratio, weak=weak)
        return result

    @staticmethod
    def _find_separate_entity(
        repository: AddressRepository | NullAddressRepository,
        phrase: Sequence[tuple[int, Token]],
        primary_match: _EntityMatch,
        classifications: Sequence[_Classification],
        entity_type: str,
        tokens: Sequence[Token],
    ) -> _EntityMatch | None:
        """Найти вторую сущность, указанную отдельно от основной.

        Пример: "Мкр Аксай 3а, улица Абая 12" — living_area Аксай 3а,
        street_name Абая.
        """
        remaining = [
            (position, token)
            for position, token in phrase
            if not (
                token.start < primary_match.end
                and primary_match.start < token.end
            )
        ]
        city_spans = _city_context_spans(tokens)
        name_tokens = [
            token
            for _, token in remaining
            if vocab.canonical_street_type(token.raw) is None
            and (token.start, token.end) not in city_spans
        ]
        if not name_tokens or not any(
            any(ch.isalpha() for ch in token.norm) for token in name_tokens
        ):
            return None
        query_tokens = [token.norm for token in name_tokens]
        match, _, _ = AddressParserService._lookup_streets(
            repository,
            query_tokens,
            name_tokens,
            classifications,
            preferred_type=entity_type,
        )
        if match is None or match.entity.entity_type != entity_type:
            return None
        return match

    @staticmethod
    def _lookup_streets(
        repository: AddressRepository | NullAddressRepository,
        query_tokens: Sequence[str],
        name_tokens: Sequence[Token],
        classifications: Sequence[_Classification],
        preferred_type: str | None = None,
    ) -> tuple[_EntityMatch | None, str, list[StreetEntity]]:
        """Поиск с постепенным ослаблением фильтров и валидацией кандидатов.

        Возвращает (совпадение, уровень фильтра, кандидаты). Уровень фильтра:
        `city_district`, `city`, `district` или `none`.

        Важно: непустой ответ FTS/OR ещё не означает совпадение. Каждый
        уровень фильтров должен пройти `_match_entity` (порог 0.8), иначе
        идём дальше и снимаем фильтр города/района.
        """
        city_ids = [c.item.id for c in classifications if c.kind == "city"] or None
        district_ids = [c.item.id for c in classifications if c.kind == "district"] or None
        attempts: list[tuple[dict[str, list[int] | None], str]] = []
        possible: list[tuple[list[int] | None, list[int] | None, str]] = []
        if city_ids:
            possible.append((city_ids, district_ids, "city_district"))
            possible.append((city_ids, None, "city"))
        if district_ids:
            possible.append((None, district_ids, "district"))
        # Без явных фильтров это именно уровень "none", а не city_district:
        # от него зависит размер пула кандидатов и проверка неоднозначности.
        possible.append((None, None, "none"))
        for city, district, level in possible:
            filters = {"city_ids": city, "district_ids": district}
            if all(item[0] != filters for item in attempts):
                attempts.append((filters, level))

        for filters, level in attempts:
            # Без фильтра города FTS может вернуть много шумовых совпадений.
            # Берём больше кандидатов, чтобы не потерять одноимённые улицы
            # в других городах и корректно определить неоднозначность.
            search_limit = 32 if level == "none" else 8
            found = repository.search_streets(
                query_tokens, limit=search_limit, **filters  # type: ignore[arg-type]
            )
            if not found:
                continue
            matched_pool = found
            if preferred_type is not None:
                # "66 квартал" не должен матчиться на улицу "66", а
                # "Мкр Аксай 3а" — на улицу "Аксай".
                typed = [item for item in found if item.entity_type == preferred_type]
                if typed:
                    matched_pool = typed
            match = _match_entity(name_tokens, matched_pool)
            if match is not None:
                # Возвращаем полный пул: по нему проверяем неоднозначность
                # и несвязанные с городом ведущие названия.
                return match, level, found
        return None, "none", []

    @staticmethod
    def _apply_entity(
        result: _CoreResult,
        entity: StreetEntity,
        index: ClassifierIndex,
        name_slice: str,
        *,
        trusted: bool = True,
    ) -> None:
        """Заполнение полей по найденной сущности БД (название — слайс строки)."""
        if name_slice:
            if entity.entity_type == "living_area":
                result.living_area = name_slice
            else:
                result.street_name = name_slice

        if not trusted:
            # Неоднозначное/несвязанное совпадение: не подставляем ни
            # родительские сущности, ни тип из случайного города.
            return

        # Внутренние id — только для доверенного совпадения.
        result.street_id = entity.id
        result.street_entity_type = entity.entity_type

        # Тип из БД имеет приоритет над регуляркой (ТЗ, шаг 3).
        db_type = vocab.canonical_street_type(entity.street_type) or entity.street_type
        if db_type:
            result.street_type = db_type

        # Обогащение родительскими сущностями.
        if result.city is None and entity.city_id is not None:
            city = index.city_by_id.get(entity.city_id)
            if city:
                result.city = city.name
        if result.district is None and entity.district_id is not None:
            district = index.district_by_id.get(entity.district_id)
            if district and not _is_pseudo_district(district.name):
                # Район не должен дублировать город ("Алматы" / "Тараз").
                if result.city is None or normalize(district.name) != normalize(result.city):
                    result.district = district.name
        if result.province is None and entity.province_id is not None:
            province = index.province_by_id.get(entity.province_id)
            if province:
                result.province = province.name

        # id родительских сущностей нужны для геокодинга (центроиды).
        if result.city_id is None and entity.city_id is not None:
            result.city_id = entity.city_id
        if result.district_id is None and entity.district_id is not None:
            result.district_id = entity.district_id
        if result.province_id is None and entity.province_id is not None:
            result.province_id = entity.province_id

    @staticmethod
    def _fill_province_from_city(result: _CoreResult, index: ClassifierIndex) -> None:
        """Авто-обогащение province по найденному городу.

        Сначала справочник `cities` (province_id), затем города республиканского
        значения, у которых провинция совпадает с городом (ТЗ, п.3).
        """
        if result.province is not None or not result.city:
            return
        city = index.city_by_norm.get(normalize(result.city))
        if city is not None and city.parent_id is not None:
            province = index.province_by_id.get(city.parent_id)
            if province is not None:
                result.province = province.name
                if result.province_id is None:
                    result.province_id = province.id
                return
        if result.city.lower() in REPUBLICAN_CITIES:
            result.province = result.city

    @staticmethod
    def _confidence(
        repository: AddressRepository | NullAddressRepository,
        result: _CoreResult,
        ratio: float,
        weak: bool = False,
    ) -> float:
        if not repository.db_loaded:
            # БД отсутствует: базовый регулярный разбор, строго 0.5.
            return 0.5
        resolved = sum(
            1 for value in (result.province, result.city, result.district) if value
        )
        if weak:
            # Неоднозначная улица без города в тексте: 0.5…0.6 (ТЗ, P0-3.2).
            return round(min(0.6, 0.5 + 0.02 * resolved), 2)
        if result.matched:
            if ratio >= 0.95:
                base = 0.9
            elif ratio >= 0.8:
                base = 0.8
            else:
                base = 0.62
        else:
            base = 0.5
        return round(min(0.99, base + 0.03 * resolved), 2)


__all__ = ["AddressParserService", "ParsedAddress"]
