"""Тесты Tail Parser: вырезание дома, квартиры, подъезда, этажа, ориентира."""

from __future__ import annotations

import pytest

from app.core.tail_parser import parse_tail
from app.core.text import WorkText


def _tail(raw: str):
    work = WorkText(raw)
    spans = parse_tail(work)
    return work, spans


def test_house_keyword():
    _, spans = _tail("Абая, дом 12а")
    assert spans.house == "12а"


def test_house_fraction_and_letter():
    _, spans = _tail("Абая 55/6")
    assert spans.house == "55/6"


def test_apartment_keywords():
    for raw in ("Абая 12 кв 45", "Абая 12 кв. 45", "Абая 12 квартира 45", "Абая 12 flat 45"):
        _, spans = _tail(raw)
        assert spans.apartment == "45", raw
        assert spans.house == "12", raw


def test_entrance_and_floor():
    _, spans = _tail("Абая 12, подъезд 4, этаж 9")
    assert (spans.house, spans.entrance, spans.floor) == ("12", "4", "9")


def test_short_forms():
    _, spans = _tail("Абая 12, под. 2, эт. 7")
    assert (spans.entrance, spans.floor) == ("2", "7")


def test_landmark_noun():
    _, spans = _tail("Абая 12, магазин Береке")
    assert spans.landmark == "магазин Береке"
    assert spans.house == "12"


def test_landmark_qazpost():
    _, spans = _tail("Абая 12, Qazpost")
    assert spans.landmark == "Qazpost"


def test_positional_fallback():
    # ТЗ, шаг 4: первая цифра -> дом, далее подъезд, этаж
    _, spans = _tail("Абая 55/6 4 9")
    assert spans.house == "55/6"
    assert spans.entrance == "4"
    assert spans.floor == "9"


def test_core_number_not_treated_as_house():
    _, spans = _tail("12 микрорайон, дом 5")
    assert spans.house == "5"
    assert spans.protected


def test_no_house_when_only_area_number():
    _, spans = _tail("66 квартал")
    assert spans.house is None


def test_house_number_first_with_keyword():
    _, spans = _tail("Абая, 12 дом")
    assert spans.house == "12"


@pytest.mark.parametrize(
    "raw",
    ["", "Алматы", "просто текст без чисел"],
)
def test_nothing_to_extract(raw):
    _, spans = _tail(raw)
    assert spans.as_dict() == {
        "landmark": None, "house": None, "apartment": None,
        "entrance": None, "floor": None,
    }
