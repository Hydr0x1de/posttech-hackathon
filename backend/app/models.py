"""Pydantic-схемы API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.parser import ParsedAddress


class AddressRecord(BaseModel):
    """Входная запись пакета."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)

    id: str = Field(..., min_length=1, max_length=128, description="Идентификатор записи")
    raw_address: str = Field(..., max_length=2048, description="Адрес как его ввёл пользователь")

    @field_validator("raw_address", mode="before")
    @classmethod
    def _coerce_none(cls, value: object) -> str:
        return "" if value is None else str(value)


class BatchRequest(BaseModel):
    """Альтернативная форма тела пакетного запроса: `{"items": [...]}`."""

    model_config = ConfigDict(extra="ignore")

    items: list[AddressRecord] = Field(
        default_factory=list, description="Записи для разбора"
    )


class ParsedAddressModel(BaseModel):
    """Результат разбора одной записи.

    Все текстовые поля — слайсы `raw_address` (без исправления опечаток);
    `province`/`city`/`district`/`street_type` — канонические значения из БД.
    """

    model_config = ConfigDict(extra="forbid")

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
    confidence: float = Field(0.5, ge=0.0, le=1.0)

    @classmethod
    def from_result(cls, result: ParsedAddress) -> ParsedAddressModel:
        return cls(**result.to_dict())


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    db_loaded: bool
    db_path: str | None = None
    db_file_exists: bool = False
    fts: bool = False
    provinces: int = 0
    cities: int = 0
    districts: int = 0
    streets: int = 0
