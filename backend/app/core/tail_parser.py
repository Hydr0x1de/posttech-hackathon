"""Tail Parser: вырезание структурных элементов адреса (ТЗ, шаг 2 и 4).

Правила:
* все значения — слайсы исходной строки, регистр/опечатки не исправляются;
* найденный спан маскируется (затирается пробелами), чтобы он не попал
  в запрос к БД и не был найден повторно другой грамматикой;
* числа, принадлежащие названию ("12 микрорайон", "66 квартал"),
  защищаются от трактовки как дом.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.core import vocabulary as vocab
from app.core.text import WorkText, iter_tokens


@dataclass(slots=True)
class GluedHint:
    """Слитное написание, где число однозначно принадлежит ключевому слову.

    `start`/`end` — спан в рабочей копии строки, охватывающий пару
    "ключевое слово + число" ("12дом", "flat191").
    """

    field: str
    value: str
    start: int
    end: int


@dataclass(slots=True)
class TailSpans:
    """Результат вырезания хвостовой части адреса."""

    house: str | None = None
    apartment: str | None = None
    entrance: str | None = None
    floor: str | None = None
    landmark: str | None = None
    protected: list[tuple[int, int]] = field(default_factory=list)

    def as_dict(self) -> dict[str, str | None]:
        return {
            "landmark": self.landmark,
            "house": self.house,
            "apartment": self.apartment,
            "entrance": self.entrance,
            "floor": self.floor,
        }


def _take_first(work: WorkText, pattern, target: str, spans: TailSpans) -> bool:  # noqa: ANN001
    for match in pattern.finditer(work.text):
        value = work.original_slice(match.start("num"), match.end("num"))
        if not value:
            continue
        setattr(spans, target, value)
        work.mask(match.start(), match.end())
        return True
    return False


def _find_protected_numbers(work: WorkText) -> list[tuple[int, int]]:
    """Спаны "Микрорайон 12" / "66 квартал" — числа относятся к названию."""
    protected: list[tuple[int, int]] = []
    for pattern in vocab.CORE_NUMBER_PATTERNS:
        for match in pattern.finditer(work.text):
            protected.append((match.start(), match.end()))
    return protected


def _overlaps(span: tuple[int, int], others: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and other_start < end for other_start, other_end in others)


def _take_landmark(work: WorkText, spans: TailSpans) -> None:
    """Ориентир: "магазин Береке", "Qazpost", "напротив школы №5"."""
    text = work.text
    tokens = list(iter_tokens(text))

    def expand(match, *, include_keyword: bool) -> tuple[int, int] | None:  # noqa: ANN001
        end = match.end()
        extra = 0
        for token in tokens:
            if token.start < end:
                continue
            # Между словами допустимы только разделители; затёртые
            # (уже разобранные) участки означают конец ориентира.
            if work.has_masked_in(end, token.start):
                break
            if text[end:token.start].strip(" .,-–—") != "":
                break
            lower = token.norm
            if lower in vocab.STOP_WORDS or vocab.STREET_TYPE_RE.fullmatch(token.raw):
                break
            if token.raw[0].isdigit():
                break
            end = token.end
            extra += 1
            if extra >= 3:
                break
        if extra == 0:
            # "Qazpost" / "Казпочта" — ориентир без уточняющего слова.
            if include_keyword and match.group("kw").lower() in vocab.SELF_CONTAINED_LANDMARKS:
                return (match.start(), match.end())
            return None
        start = match.start() if include_keyword else end
        if not include_keyword:
            # Предлог не включаем в landmark — берём только само название.
            for token in tokens:
                if token.start >= match.end():
                    start = token.start
                    break
        return (start, end) if start < end else None

    for pattern, include_keyword in ((vocab.LANDMARK_RE, True), (vocab.LANDMARK_PREP_RE, False)):
        for match in pattern.finditer(text):
            # "ЖК" / "микрорайон" — это тип жилого массива, а не ориентир.
            if vocab.STREET_TYPE_RE.fullmatch(match.group("kw")):
                continue
            span = expand(match, include_keyword=include_keyword)
            if span:
                spans.landmark = work.original_slice(*span)
                work.mask(*span)
                return


def _tail_number_run(
    work: WorkText, protected: list[tuple[int, int]]
) -> list[tuple[str, tuple[int, int]]]:
    """Непрерывная последовательность чисел в конце адреса (после названия).

    Собираем токены с конца, пока не встретим слово (название улицы) или
    защищённый спан.
    """
    tokens = list(iter_tokens(work.text))
    run: list[tuple[str, tuple[int, int]]] = []
    for token in reversed(tokens):
        span = (token.start, token.end)
        if _overlaps(span, protected):
            break
        if vocab.BARE_NUMBER_RE.fullmatch(token.raw):
            run.append((token.raw, span))
            continue
        break
    run.reverse()
    return run


def parse_tail(work: WorkText, hints: Sequence[GluedHint] = ()) -> TailSpans:
    """Полный разбор хвоста: квартира, подъезд, этаж, ориентир, дом."""
    spans = TailSpans()

    # 0. Слитные написания ("12дом", "4подьезд", "flat191") — самый сильный
    #    сигнал: число стоит вплотную к ключевому слову.
    for hint in hints:
        if work.has_masked_in(hint.start, hint.end):
            continue
        if hint.field == "living_area":
            spans.protected.append((hint.start, hint.end))
            continue
        if getattr(spans, hint.field) is None and hint.value:
            setattr(spans, hint.field, hint.value)
        work.mask(hint.start, hint.end)

    # 1. Квартира / подъезд / этаж — сначала ключевые слова, затем постфиксная
    #    форма ("15 кв", "4 подъезд", "3 эт").
    for pattern in (vocab.APARTMENT_RE, vocab.APARTMENT_POSTFIX_RE):
        if _take_first(work, pattern, "apartment", spans):
            break
    # Постфиксные формы ("2 подъезд", "5 этаж") проверяются раньше префиксных:
    # иначе "2 подъезд 5 этаж" разбирается как entrance=5, floor=нет.
    for pattern in (vocab.ENTRANCE_POSTFIX_RE, vocab.ENTRANCE_RE):
        if _take_first(work, pattern, "entrance", spans):
            break
    for pattern in (vocab.FLOOR_POSTFIX_RE, vocab.FLOOR_RE):
        if _take_first(work, pattern, "floor", spans):
            break

    # 2. Ориентир.
    _take_landmark(work, spans)

    # 3. Числа в названии ("12 микрорайон") — защищаем от роли дома.
    protected = list(spans.protected) + _find_protected_numbers(work)
    spans.protected = protected

    # 4. Дом по ключевому слову ("дом 12", "д. 12а", "12 дом").
    if not _take_first(work, vocab.HOUSE_RE, "house", spans):
        _take_first(work, vocab.HOUSE_POSTFIX_RE, "house", spans)

    # 5. Позиционный фоллбэк (ТЗ, шаг 4): первое «голое» число — дом,
    #    далее по порядку подъезд, этаж, квартира.
    run = _tail_number_run(work, protected)
    if run:
        queue = list(run)
        if spans.house is None:
            raw, span = queue.pop(0)
            spans.house = work.original_slice(*span)
        for target in ("entrance", "floor", "apartment"):
            if not queue:
                break
            if getattr(spans, target) is not None:
                continue
            raw, span = queue.pop(0)
            setattr(spans, target, work.original_slice(*span))
        # Все разобранные числа убираем из запроса к БД.
        for _, span in run:
            work.mask(*span)

    return spans
