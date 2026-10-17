"""HTTP-эндпоинты API v1."""

from __future__ import annotations

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.models import AddressRecord, BatchRequest, HealthResponse, ParsedAddressModel
from app.services.parser import AddressParserService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["address"])


def _parser(request: Request) -> AddressParserService:
    return request.app.state.parser


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post(
    "/parse/batch",
    response_model=list[ParsedAddressModel],
    response_model_exclude_none=False,
    summary="Пакетный разбор адресов",
    description=(
        "Принимает массив записей `[{id, raw_address}]` (или объект "
        "`{items: [...]}`) и возвращает массив разобранных адресов в том же "
        "порядке. Текстовые поля — слайсы исходной строки без исправления "
        "опечаток. Заголовки ответа: `X-Records`, `X-Elapsed-Ms`."
    ),
)
async def parse_batch(
    request: Request,
    response: Response,
    payload: Annotated[
        list[AddressRecord] | BatchRequest,
        Body(
            description=(
                "Массив записей `[{id, raw_address}]` или объект "
                "`{\"items\": [{id, raw_address}]}`."
            ),
            examples=[
                [{"id": "rec_001", "raw_address": "г. Актобе, ул. Абая, д. 55/6, кв 12"}],
                {
                    "items": [
                        {
                            "id": "rec_001",
                            "raw_address": "г. Актобе, ул. Абая, д. 55/6, кв 12",
                        }
                    ]
                },
            ],
        ),
    ],
) -> list[ParsedAddressModel]:
    settings = _settings(request)
    records = payload.items if isinstance(payload, BatchRequest) else payload
    if len(records) > settings.max_batch_size:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"Пакет слишком большой: {len(records)} записей, "
                f"максимум {settings.max_batch_size}"
            ),
        )

    started = time.perf_counter()
    parser = _parser(request)
    # Разбор CPU-bound: уводим из event loop, чтобы не блокировать другие запросы.
    results = await run_in_threadpool(
        parser.parse_batch, [(record.id, record.raw_address) for record in records]
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Elapsed-Ms"] = f"{elapsed_ms:.1f}"
    response.headers["X-Records"] = str(len(results))
    logger.info("parse/batch: %d записей за %.1f мс", len(results), elapsed_ms)
    return [ParsedAddressModel.from_result(item) for item in results]


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Состояние сервиса",
    description="Статус сервиса и флаг подключения базы адресов.",
)
async def health(request: Request) -> HealthResponse:
    manager = request.app.state.repository_manager
    settings = _settings(request)
    payload = await run_in_threadpool(manager.health)
    return HealthResponse(
        status="ok",
        version=settings.version,
        db_loaded=bool(payload.get("db_loaded")),
        db_path=payload.get("db_path"),  # type: ignore[arg-type]
        db_file_exists=bool(payload.get("db_file_exists")),
        fts=bool(payload.get("fts")),
        provinces=int(payload.get("provinces") or 0),  # type: ignore[arg-type]
        cities=int(payload.get("cities") or 0),  # type: ignore[arg-type]
        districts=int(payload.get("districts") or 0),  # type: ignore[arg-type]
        streets=int(payload.get("streets") or 0),  # type: ignore[arg-type]
    )
