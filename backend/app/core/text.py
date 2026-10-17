"""Работа с текстом адреса без потери смещений (offsets).

ТЗ, п.2: все текстовые поля результата обязаны быть слайсами исходной строки
(`raw_address[start:end]`), поэтому любая модификация рабочей копии должна
сохранять карту "индекс рабочей строки -> индекс исходной строки".

Единственный вид модификации, который здесь разрешён, — ВСТАВКА пробела.
Она позволяет разлеплять слитные написания ("66квартал" -> "66 квартал"),
не сдвигая соответствие символов. Удаление/замена символов не выполняется:
вместо этого найденные спаны "затираются" пробелами (маскирование), что
сохраняет длину строки и валидность карты.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Нормализация (только для поиска, никогда для выдачи в ответе)
# --------------------------------------------------------------------------- #

# Казахские буквы включены, чтобы не разрезать слова вида "ауданы", "көшесі".
_NON_TOKEN_RE = re.compile(r"[^0-9a-zа-яёәғқңөұүһі]+")

# Буквы, которые встречаются только в казахском алфавите. Нужны для отсечения
# казахской половины двуязычных склеек ("Актобе/Ақтөбе").
_KAZAKH_ONLY = frozenset("әғқңөұүһі")

# Латинские гомоглифы -> кириллица (второй проход поиска, см. repository).
_HOMOGLYPHS = str.maketrans(
    {
        "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к",
        "m": "м", "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
        "u": "и", "j": "й", "q": "к", "w": "в", "z": "з", "d": "д",
        "f": "ф", "g": "г", "i": "и", "l": "л", "n": "п", "r": "р",
        "s": "с", "v": "в",
    }
)

# Казахские буквы -> ближайшие русские. Нужно ТОЛЬКО для поиска/сравнения:
# пользователь пишет "Мәңгілік Ел", а в базе встречается "Мангилик ел".
_KAZAKH_FOLD = str.maketrans(
    {
        "ә": "а", "ғ": "г", "қ": "к", "ң": "н", "ө": "о",
        "ұ": "у", "ү": "у", "һ": "х", "і": "и",
    }
)


def normalize(value: str) -> str:
    """Нижний регистр, ё->е, пунктуация -> пробел, схлопывание пробелов."""
    if not value:
        return ""
    return _NON_TOKEN_RE.sub(" ", value.lower().replace("ё", "е")).strip()


def tokenize(value: str) -> list[str]:
    return [t for t in normalize(value).split(" ") if t]


def fold_homoglyphs(value: str) -> str:
    """Латиница -> кириллица (для второго прохода поиска по БД)."""
    return value.translate(_HOMOGLYPHS)


def fold_kazakh(value: str) -> str:
    """Казахские буквы -> русские (только для поиска, не для ответа)."""
    return value.translate(_KAZAKH_FOLD)


def has_latin(value: str) -> bool:
    return any("a" <= ch <= "z" for ch in value)


def is_kazakh_token(token: str) -> bool:
    """Токен содержит буквы, которых нет в русском алфавите."""
    return any(ch in _KAZAKH_ONLY for ch in token)


# Токен адреса: слово, число, дробь/литер ("55/6", "12а").
_WORD_RE = re.compile(
    r"[0-9a-zа-яёәғқңөұүһі]+(?:[/\-][0-9a-zа-яёәғқңөұүһі]+)*",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Token:
    """Токен рабочей копии со смещениями и нормализованным значением."""

    raw: str
    norm: str
    start: int
    end: int


def iter_tokens(value: str) -> Iterator[Token]:
    for match in _WORD_RE.finditer(value):
        raw = match.group(0)
        yield Token(
            raw=raw,
            norm=raw.lower().replace("ё", "е"),
            start=match.start(),
            end=match.end(),
        )


def slice_original(original: str, start: int, end: int) -> str:
    """Слайс исходной строки с обрезкой краевой пунктуации/пробелов."""

    fragment = original[start:end]
    trimmed = fragment.strip(" \t\n\r,.;:№#-—–")
    return trimmed


# --------------------------------------------------------------------------- #
# Рабочая копия строки с картой смещений
# --------------------------------------------------------------------------- #


class WorkText:
    """Рабочая копия адреса, связанная с исходной строкой."""

    __slots__ = ("original", "text", "_map", "_masked")

    def __init__(self, original: str) -> None:
        self.original = original
        self.text = original
        # _map[i] — индекс символа исходной строки для i-го символа рабочей копии
        self._map = list(range(len(original)))
        # интервалы, затёртые пробелами (уже разобранные части адреса)
        self._masked: list[tuple[int, int]] = []

    # -- вставки ---------------------------------------------------------- #

    def insert_spaces(self, positions: Iterable[int]) -> None:
        """Вставить одиночные пробелы в указанные позиции рабочей копии.

        Вставки применяются от конца к началу, поэтому исходные позиции
        остаются валидными, а карта смещений дополняется корректно.
        """
        for pos in sorted({p for p in positions if 0 < p < len(self.text)}, reverse=True):
            anchor = self._map[pos] if pos < len(self._map) else self._map[-1]
            self.text = f"{self.text[:pos]} {self.text[pos:]}"
            self._map.insert(pos, anchor)

    # -- маскирование ------------------------------------------------------ #

    def mask(self, start: int, end: int) -> None:
        """Затереть спан пробелами (длина строки сохраняется)."""
        if start >= end:
            return
        chunk = self.text[start:end]
        # Перенос строки оставляем, чтобы не склеивать строки в регулярках.
        self.text = self.text[:start] + (" " * len(chunk)) + self.text[end:]
        self._masked.append((start, end))

    def has_masked_in(self, start: int, end: int) -> bool:
        """Пересекается ли интервал с уже разобранной (затёртой) частью."""
        return any(s < end and start < e for s, e in self._masked)

    # -- смещения ---------------------------------------------------------- #

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        """Перевод спана рабочей копии в спан исходной строки."""
        if start >= end:
            return (0, 0)
        last = min(end, len(self._map)) - 1
        first = min(start, len(self._map) - 1)
        return (self._map[first], self._map[last] + 1)

    def original_slice(self, start: int, end: int) -> str:
        o_start, o_end = self.original_span(start, end)
        return slice_original(self.original, o_start, o_end)

    # -- удобные обёртки поиска -------------------------------------------- #

    def finditer(self, pattern: re.Pattern[str]) -> Iterator[re.Match[str]]:
        return pattern.finditer(self.text)

    def __len__(self) -> int:
        return len(self.text)

    def __repr__(self) -> str:  # pragma: no cover - отладка
        return f"WorkText({self.text!r})"
