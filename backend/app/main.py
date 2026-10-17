"""Точка входа FastAPI-приложения."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.v1 import health as health_handler
from app.api.v1 import router as api_router
from app.config import Settings, get_settings
from app.models import HealthResponse
from app.repositories.repository import RepositoryManager
from app.services.parser import AddressParserService

logger = logging.getLogger("app")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    _configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager = RepositoryManager(
            settings.db_path, reload_interval=settings.db_reload_interval
        )
        app.state.settings = settings
        app.state.repository_manager = manager
        app.state.parser = AddressParserService(
            manager.get,
            workers=settings.workers,
        )
        health = manager.health()
        if health.get("db_loaded"):
            logger.info("Сервис запущен с базой %s", settings.db_path)
        else:
            logger.warning(
                "База %s не найдена — активен fallback-режим (regex, confidence=0.5)",
                settings.db_path,
            )
        try:
            yield
        finally:
            manager.close()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=(
            "Разбор адресов Казахстана: структурные элементы вырезаются как слайсы "
            "исходной строки, классификаторы стандартизируются по базе адресов."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        # Чтобы фронтенд мог читать метрики пакета из JS.
        expose_headers=["X-Records", "X-Elapsed-Ms"],
    )
    app.include_router(api_router)
    # Короткий алиас из ТЗ/чек-листа: GET /health.
    app.add_api_route(
        "/health",
        health_handler,
        methods=["GET"],
        response_model=HealthResponse,
        include_in_schema=False,
    )

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": settings.app_name,
            "version": settings.version,
            "docs": "/docs",
            "health": "/api/v1/health",
        }

    return app


app = create_app()
