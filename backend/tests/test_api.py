"""Тесты HTTP API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _client(db_path: str, **overrides) -> TestClient:
    settings = Settings(db_path=db_path, db_reload_interval=0.0, **overrides)
    return TestClient(create_app(settings))


@pytest.fixture()
def client_no_db(tmp_path):
    with _client(str(tmp_path / "missing.db")) as client:
        yield client


@pytest.fixture()
def client_with_db(demo_db):
    with _client(str(demo_db)) as client:
        yield client


def test_health_without_db(client_no_db):
    response = client_no_db.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["db_loaded"] is False
    assert payload["db_file_exists"] is False
    assert payload["streets"] == 0


def test_health_with_db(client_with_db):
    payload = client_with_db.get("/api/v1/health").json()
    assert payload["db_loaded"] is True
    assert payload["fts"] is True
    assert payload["streets"] > 0
    assert payload["provinces"] > 0


def test_batch_returns_array_in_order(client_with_db):
    body = [
        {"id": "rec_001", "raw_address": "Алматы, Ауэзовский район, Казыбек би 12а кв. 4"},
        {"id": "rec_002", "raw_address": "Актобе, 12 микрорайон, дом 5"},
        {"id": "rec_003", "raw_address": ""},
    ]
    response = client_with_db.post("/api/v1/parse/batch", json=body)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list) and len(data) == 3
    assert [item["id"] for item in data] == ["rec_001", "rec_002", "rec_003"]
    assert response.headers["X-Records"] == "3"

    first = data[0]
    assert set(first) == {
        "id", "raw_address", "province", "city", "district", "street_type",
        "street_name", "living_area", "landmark", "house", "apartment",
        "entrance", "floor", "confidence",
    }
    assert first["raw_address"] == body[0]["raw_address"]
    assert first["city"] == "Алматы"
    assert first["district"] == "Ауэзовский"
    assert first["street_name"] == "Казыбек би"
    assert first["house"] == "12а"
    assert data[1]["living_area"] == "12 микрорайон"


def test_batch_accepts_items_wrapper(client_with_db):
    body = {
        "items": [
            {"id": "rec_001", "raw_address": "Алматы, Абая 55"},
            {"id": "rec_002", "raw_address": ""},
        ]
    }
    response = client_with_db.post("/api/v1/parse/batch", json=body)
    assert response.status_code == 200
    data = response.json()
    assert [item["id"] for item in data] == ["rec_001", "rec_002"]
    assert response.headers["X-Records"] == "2"
    assert "X-Elapsed-Ms" in response.headers


def test_health_short_alias(client_with_db):
    short = client_with_db.get("/health")
    full = client_with_db.get("/api/v1/health")
    assert short.status_code == 200
    assert short.json()["db_loaded"] is True
    assert short.json()["streets"] == full.json()["streets"]


def test_cors_allows_frontend_and_exposes_headers(client_with_db):
    origin = "http://localhost:3000"
    preflight = client_with_db.options(
        "/api/v1/parse/batch",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"

    response = client_with_db.post(
        "/api/v1/parse/batch",
        json=[{"id": "rec_001", "raw_address": "Алматы, Абая 55"}],
        headers={"Origin": origin},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "x-records" in exposed
    assert "x-elapsed-ms" in exposed


def test_batch_empty(client_no_db):
    response = client_no_db.post("/api/v1/parse/batch", json=[])
    assert response.status_code == 200
    assert response.json() == []


def test_batch_validation_error(client_no_db):
    response = client_no_db.post("/api/v1/parse/batch", json=[{"raw_address": "Абая 1"}])
    assert response.status_code == 422


def test_batch_too_large(demo_db):
    with _client(str(demo_db), max_batch_size=2) as client:
        body = [{"id": f"rec_{i}", "raw_address": "Абая 1"} for i in range(3)]
        response = client.post("/api/v1/parse/batch", json=body)
        assert response.status_code == 413


def test_null_raw_address_is_allowed(client_no_db):
    response = client_no_db.post(
        "/api/v1/parse/batch", json=[{"id": "rec_1", "raw_address": None}]
    )
    assert response.status_code == 200
    assert response.json()[0]["raw_address"] == ""
