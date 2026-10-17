"""Конфигурация сервиса (переменные окружения, без внешних зависимостей)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


_DEFAULTS = {
    "app_name": "Jana Address Parser",
    "version": "1.0.0",
    "db_path": "data/address_base.db",
    "max_batch_size": 5000,
    "workers": 4,
    "cache_size": 200_000,
    "db_reload_interval": 5.0,
    "log_level": "INFO",
}


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "Jana Address Parser"
    version: str = "1.0.0"
    # Путь к SQLite-базе адресов. Файл может отсутствовать — сервис продолжит
    # работу в fallback-режиме.
    db_path: str = "data/address_base.db"
    # Максимальный размер пакета в одном запросе.
    max_batch_size: int = 5000
    # Количество потоков обработки пакета.
    workers: int = 4
    # Размер LRU-кэша разобранных адресов (на процесс).
    cache_size: int = 200_000
    # Как часто проверять появление/обновление файла БД, сек.
    db_reload_interval: float = 5.0
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        cpu = os.cpu_count() or 1
        return cls(
            app_name=os.getenv("APP_NAME", _DEFAULTS["app_name"]),
            version=os.getenv("APP_VERSION", _DEFAULTS["version"]),
            db_path=os.getenv("ADDRESS_DB_PATH", _DEFAULTS["db_path"]),
            max_batch_size=_int_env("MAX_BATCH_SIZE", _DEFAULTS["max_batch_size"]),
            workers=min(_int_env("PARSER_WORKERS", min(4, cpu)), 32),
            cache_size=_int_env("PARSER_CACHE_SIZE", _DEFAULTS["cache_size"]),
            db_reload_interval=_float_env("DB_RELOAD_INTERVAL", _DEFAULTS["db_reload_interval"]),
            log_level=os.getenv("LOG_LEVEL", _DEFAULTS["log_level"]).upper(),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
