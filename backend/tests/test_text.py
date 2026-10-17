"""Тесты текстового слоя: сохранение смещений исходной строки."""

from __future__ import annotations

import pytest

from app.core.text import WorkText, is_kazakh_token, iter_tokens, normalize, tokenize
from app.core.vocabulary import GLUED_KEYWORD_DIGIT_RE, GLUED_NUMBER_KEYWORD_RE


def test_insert_spaces_keeps_offsets():
    work = WorkText("66квартал")
    work.insert_spaces([2])
    assert work.text == "66 квартал"
    # "квартал" в исходной строке начинается с индекса 2
    tokens = list(iter_tokens(work.text))
    assert [t.raw for t in tokens] == ["66", "квартал"]
    assert work.original_slice(tokens[1].start, tokens[1].end) == "квартал"
    assert work.original_slice(tokens[0].start, tokens[0].end) == "66"


def test_insert_multiple_spaces_descending():
    raw = "12дом flat191"
    positions = [m.start("kw") for m in GLUED_NUMBER_KEYWORD_RE.finditer(raw)]
    positions += [m.start("num") for m in GLUED_KEYWORD_DIGIT_RE.finditer(raw)]
    work = WorkText(raw)
    work.insert_spaces(positions)
    assert work.text == "12 дом flat 191"
    tokens = list(iter_tokens(work.text))
    assert tokens[-1].raw == "191"
    assert work.original_slice(tokens[-1].start, tokens[-1].end) == "191"


def test_mask_keeps_length_and_offsets():
    raw = "Абая 55/6 кв 12"
    work = WorkText(raw)
    work.mask(9, 14)  # "кв 12"
    assert len(work.text) == len(raw)
    assert "кв" not in work.text
    assert work.has_masked_in(9, 14)
    assert not work.has_masked_in(0, 8)
    # смещения не поехали
    assert work.original_slice(0, 4) == "Абая"


def test_slice_original_trims_edge_punctuation():
    work = WorkText("г. Актобе, ул. Абая")
    assert work.original_slice(3, 9) == "Актобе"
    # запятая и точка по краям спана отбрасываются
    assert work.original_slice(9, 14) == "ул"


def test_normalize_and_tokenize():
    # пунктуация (в том числе дробная черта) для поиска заменяется пробелом
    assert normalize("Г. Актобе, ул. Абая 55/6") == "г актобе ул абая 55 6"
    assert tokenize("Абая 55/6") == ["абая", "55", "6"]
    assert normalize("") == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Ақтөбе", True), ("Актобе", False), ("көшесі", True), ("улица", False)],
)
def test_is_kazakh_token(value, expected):
    assert is_kazakh_token(value) is expected


def test_original_span_is_identity_without_changes():
    work = WorkText("улица Абая")
    assert work.original_span(0, 5) == (0, 5)
