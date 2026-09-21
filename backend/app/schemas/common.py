"""Shared Pydantic schemas for the POLARIS API."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")

#: The four data classes POLARIS distinguishes everywhere in the UI.
DataClass = Literal[
    "REAL_LIVE_WEATHER",
    "MODELLED_ENERGY",
    "AI_FORECAST",
    "SIMULATED_SCENARIO",
]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Provenance(BaseModel):
    """Attached to every payload so the UI can never mislabel a number."""

    data_class: DataClass
    label: str
    description: str
    is_live: bool = False
    is_measured: bool = False
    source: str | None = None
    disclaimer: str | None = None


PROVENANCE_REAL = Provenance(
    data_class="REAL_LIVE_WEATHER",
    label="REAL LIVE WEATHER DATA",
    description=(
        "Measured observations retrieved from an external meteorological "
        "provider."
    ),
    is_live=True,
    is_measured=True,
)

PROVENANCE_MODELLED = Provenance(
    data_class="MODELLED_ENERGY",
    label="RESEARCH-BASED ENERGY MODEL",
    description=(
        "Estimated from live weather using a physics-based station energy "
        "model. Not telemetry."
    ),
    is_live=False,
    is_measured=False,
    disclaimer=(
        "Station energy parameters are modelled/estimated for research and "
        "demonstration purposes."
    ),
)

PROVENANCE_FORECAST = Provenance(
    data_class="AI_FORECAST",
    label="AI FORECAST",
    description=(
        "Predicted by scikit-learn models from real weather inputs."
    ),
    is_live=False,
    is_measured=False,
)

PROVENANCE_SIMULATED = Provenance(
    data_class="SIMULATED_SCENARIO",
    label="SIMULATED SCENARIO",
    description="What-if scenario output. Not live station telemetry.",
    is_live=False,
    is_measured=False,
    disclaimer="SIMULATED SCENARIO - NOT LIVE STATION TELEMETRY",
)


class Envelope(BaseModel, Generic[T]):
    """Standard response wrapper carrying provenance with the payload."""

    data: T
    provenance: Provenance
    generated_at: datetime
    notice: str | None = None


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    code: str | None = None
    path: str | None = None
    timestamp: datetime | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "error"]
    app: str
    version: str
    database_connected: bool
    database_version: str | None = None
    database_dsn: str | None = None
    scheduler_running: bool
    models_trained: bool
    live_weather_status: str | None = None
    last_weather_update: datetime | None = None
    weather_age_seconds: float | None = None
    active_source: str | None = None
    uptime_seconds: float
    timestamp: datetime
