"""Тесты forward-геокодинга: дом -> улица -> район -> город -> область."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.geocode import house_lookup_key, normalize_house

PARSE_FIELDS = {
    "id", "raw_address", "province", "city", "district", "street_type",
    "street_name", "living_area", "landmark", "house", "apartment",
    "entrance", "floor", "confidence",
}
GEOCODE_FIELDS = {"id", "lat", "lon", "precision", "source"}


def _client(db_path: str) -> TestClient:
    settings = Settings(db_path=db_path, db_reload_interval=0.0)
    return TestClient(create_app(settings))


@pytest.fixture()
def client(demo_db):
    with _client(str(demo_db)) as test_client:
        yield test_client


@pytest.fixture()
def client_no_db(tmp_path):
    with _client(str(tmp_path / "missing.db")) as test_client:
        yield test_client


def _geocode(client: TestClient, **payload) -> dict:
    body = {"id": "rec_001", **payload}
    response = client.post("/api/v1/geocode", json=body)
    assert response.status_code == 200
    return response.json()


# --------------------------------------------------------------------------- #
# Нормализация номера дома
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("55/6", "55/6"),
        ("55 / 6", "55/6"),
        ("12А", "12а"),
        ("1/2", "1/2"),
        ("12", "12"),
        ("  109 / 9 к15 ", "109/9 к15"),
    ],
)
def test_normalize_house_keeps_slash_and_letters(raw, expected):
    assert normalize_house(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("55/6", "556"), ("1/2", "12"), ("12А", "12а"), ("12", "12")],
)
def test_house_lookup_key_is_lossy_prefilter(raw, expected):
    assert house_lookup_key(raw) == expected


# --------------------------------------------------------------------------- #
# Уровни точности
# --------------------------------------------------------------------------- #


def test_exact_house_from_raw_address(client):
    payload = _geocode(client, raw_address="Алматы, Абая 47")
    assert payload == {
        "id": "rec_001",
        "lat": 43.2424586,
        "lon": 76.9423147,
        "precision": "house",
        "source": "osm",
    }


def test_exact_house_from_parsed_fields(client):
    payload = _geocode(client, city="Алматы", street_name="Абая", house="47")
    assert payload["precision"] == "house"
    assert payload["lat"] == pytest.approx(43.2424586)
    assert payload["lon"] == pytest.approx(76.9423147)


def test_several_houses_on_number_average_to_house_approx(client):
    payload = _geocode(client, raw_address="Алматы, Казыбек би 55")
    assert payload["precision"] == "house_approx"
    assert payload["lat"] == pytest.approx(43.255)
    assert payload["lon"] == pytest.approx(76.955)


def test_street_centroid_when_house_number_missing(client):
    payload = _geocode(client, raw_address="Алматы, Кабанбай батыра 999")
    assert payload["precision"] == "street"
    assert payload["lat"] == pytest.approx(43.2305)
    assert payload["lon"] == pytest.approx(76.9305)


def test_living_area_centroid(client):
    payload = _geocode(client, raw_address="Актобе, 12 микрорайон")
    assert payload["precision"] == "living_area"
    assert payload["lat"] == pytest.approx(50.2805)
    assert payload["lon"] == pytest.approx(57.1705)


def test_city_centroid_when_street_unknown(client):
    payload = _geocode(client, raw_address="Конаев, Неизвестная 5")
    assert payload["precision"] == "city"
    assert payload["lat"] == pytest.approx(43.87)
    assert payload["lon"] == pytest.approx(77.06)


def test_district_centroid_when_city_has_no_houses(client):
    payload = _geocode(client, raw_address="Балхаш, Балхашский район, Неизвестная 5")
    assert payload["precision"] == "district"
    assert payload["lat"] == pytest.approx(46.84)
    assert payload["lon"] == pytest.approx(74.99)


def test_province_centroid_when_city_and_district_have_no_houses(client):
    payload = _geocode(client, raw_address="Туркестан, Неизвестная 5")
    assert payload["precision"] == "province"
    assert payload["lat"] == pytest.approx(42.32)
    assert payload["lon"] == pytest.approx(69.59)


def test_unknown_address_returns_nulls_with_status_200(client):
    payload = _geocode(client, raw_address="Атлантида, Неизвестная 5")
    assert payload == {
        "id": "rec_001",
        "lat": None,
        "lon": None,
        "precision": None,
        "source": None,
    }


def test_missing_db_returns_nulls_without_failing(client_no_db):
    payload = _geocode(client_no_db, raw_address="Алматы, Абая 47")
    assert payload["lat"] is None
    assert payload["lon"] is None
    assert payload["precision"] is None
    assert payload["source"] is None


def test_db_without_houses_returns_nulls(truncated_db):
    """Пустая таблица houses — не ошибка, просто координат нет."""
    with _client(str(truncated_db)) as test_client:
        payload = _geocode(test_client, raw_address="Караганда, 12 микрорайон 5")
    assert payload["precision"] is None


# --------------------------------------------------------------------------- #
# Лоссовый housenumber_norm не должен путать 1/2 и 12
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("house", "expected_lat"),
    [("1/2", 43.241), ("12", 43.24), ("12а", 43.239)],
)
def test_strict_house_normalization_does_not_match_12_and_1_2(
    client, house, expected_lat
):
    payload = _geocode(client, city="Алматы", street_name="Абая", house=house)
    assert payload["precision"] == "house"
    assert payload["lat"] == pytest.approx(expected_lat)


def test_raw_address_one_half_is_not_house_12(client):
    payload = _geocode(client, raw_address="Алматы, Абая 1/2")
    assert payload["lat"] == pytest.approx(43.241)
    assert payload["precision"] == "house"


# --------------------------------------------------------------------------- #
# Контракт и отсутствие утечки внутренних id
# --------------------------------------------------------------------------- #


def test_geocode_response_has_no_internal_ids(client):
    payload = _geocode(client, raw_address="Алматы, Абая 47")
    assert set(payload) == GEOCODE_FIELDS
    assert "street_id" not in payload
    assert "city_id" not in payload


def test_parse_batch_contract_still_has_exactly_14_fields(client):
    response = client.post(
        "/api/v1/parse/batch",
        json=[{"id": "rec_001", "raw_address": "Алматы, Абая 47"}],
    )
    assert response.status_code == 200
    item = response.json()[0]
    assert set(item) == PARSE_FIELDS
    assert len(item) == 14
    for internal in ("street_id", "city_id", "district_id", "province_id"):
        assert internal not in item


def test_geocode_batch_preserves_order_and_headers(client):
    body = [
        {"id": "a", "raw_address": "Алматы, Абая 47"},
        {"id": "b", "city": "Алматы", "street_name": "Казыбек би", "house": "55"},
        {"id": "c", "raw_address": "Атлантида, Неизвестная 5"},
    ]
    response = client.post("/api/v1/geocode/batch", json=body)
    assert response.status_code == 200
    data = response.json()
    assert [item["id"] for item in data] == ["a", "b", "c"]
    assert [item["precision"] for item in data] == ["house", "house_approx", None]
    assert response.headers["X-Records"] == "3"
    assert "X-Elapsed-Ms" in response.headers


def test_geocode_batch_accepts_items_wrapper(client):
    response = client.post(
        "/api/v1/geocode/batch",
        json={"items": [{"id": "a", "raw_address": "Алматы, Абая 47"}]},
    )
    assert response.status_code == 200
    assert response.json()[0]["id"] == "a"
    assert response.json()[0]["precision"] == "house"


def test_geocode_batch_validation_error(client):
    response = client.post("/api/v1/geocode/batch", json=[{"city": "Алматы"}])
    assert response.status_code == 422
