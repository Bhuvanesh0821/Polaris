"""Shared column helpers and enums for POLARIS ORM models."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum as SAEnum, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    """created_at / updated_at maintained by the database."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class DataProvenance(str, enum.Enum):
    """Every row in POLARIS declares where its numbers came from.

    This is the enforcement point for the project's data-honesty rule:
    weather can be REAL, station energy state never is.

    There is deliberately NO "synthetic weather" member. POLARIS never
    fabricates a weather observation. If every live provider fails, the API
    reports a stale/failed status and re-serves the last genuinely observed
    row - it does not invent one.
    """

    LIVE_OBSERVED = "LIVE_OBSERVED"  # live measurement pulled just now
    REAL_OBSERVED = "REAL_OBSERVED"  # historical measurement / reanalysis
    REAL_FORECAST = "REAL_FORECAST"  # NWP weather forecast from a provider
    MODELLED = "MODELLED"  # physics/engineering model output
    ML_PREDICTED = "ML_PREDICTED"  # scikit-learn model output
    SIMULATED = "SIMULATED"  # crisis scenario / what-if
    OPTIMIZED = "OPTIMIZED"  # optimiser dispatch decision


REAL_WEATHER_PROVENANCE = {
    DataProvenance.LIVE_OBSERVED,
    DataProvenance.REAL_OBSERVED,
    DataProvenance.REAL_FORECAST,
}


class StationKind(str, enum.Enum):
    """Distinguishes the modelled plant from real reference observatories."""

    MODELLED_PLANT = "MODELLED_PLANT"  # real coords, MODELLED energy system
    REAL_OBSERVATION = "REAL_OBSERVATION"  # real station, weather only


class SourceHealth(str, enum.Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"  # succeeded, but on a fallback provider
    STALE = "STALE"  # no fresh data within the freshness window
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class GeneratorStatus(str, enum.Enum):
    OFFLINE = "OFFLINE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STANDBY = "STANDBY"
    FAULT = "FAULT"
    MAINTENANCE = "MAINTENANCE"


class BatteryMode(str, enum.Enum):
    CHARGING = "CHARGING"
    DISCHARGING = "DISCHARGING"
    IDLE = "IDLE"
    RESERVE = "RESERVE"
    PROTECTED = "PROTECTED"


class AlertSeverity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"


class AlertStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class ScenarioType(str, enum.Enum):
    NORMAL_OPERATION = "NORMAL_OPERATION"
    SEVERE_STORM = "SEVERE_STORM"
    LOW_SOLAR = "LOW_SOLAR"
    WIND_FAILURE = "WIND_FAILURE"
    GENERATOR_FAILURE = "GENERATOR_FAILURE"
    HIGH_DEMAND = "HIGH_DEMAND"
    COMBINED_CRISIS = "COMBINED_CRISIS"


class RecommendationCategory(str, enum.Enum):
    LOAD_MANAGEMENT = "LOAD_MANAGEMENT"
    GENERATION_DISPATCH = "GENERATION_DISPATCH"
    STORAGE_STRATEGY = "STORAGE_STRATEGY"
    FUEL_CONSERVATION = "FUEL_CONSERVATION"
    MAINTENANCE = "MAINTENANCE"
    SAFETY = "SAFETY"


class Urgency(str, enum.Enum):
    ROUTINE = "ROUTINE"
    ELEVATED = "ELEVATED"
    URGENT = "URGENT"
    IMMEDIATE = "IMMEDIATE"


# ---------------------------------------------------------------------------
# Shared PostgreSQL ENUM type objects.
#
# A native PG enum is a database-level object, so the SAME SQLAlchemy type
# instance must be reused by every column that references it. Binding it to
# Base.metadata makes create_all() emit exactly one CREATE TYPE per enum.
# ---------------------------------------------------------------------------

_MD = Base.metadata


def _pg_enum(py_enum: type[enum.Enum], name: str) -> SAEnum:
    return SAEnum(
        py_enum,
        name=name,
        metadata=_MD,
        values_callable=lambda e: [m.value for m in e],
        native_enum=True,
        create_constraint=False,
    )


PROVENANCE_ENUM = _pg_enum(DataProvenance, "data_provenance")
STATION_KIND_ENUM = _pg_enum(StationKind, "station_kind")
SOURCE_HEALTH_ENUM = _pg_enum(SourceHealth, "source_health")
GENERATOR_STATUS_ENUM = _pg_enum(GeneratorStatus, "generator_status")
BATTERY_MODE_ENUM = _pg_enum(BatteryMode, "battery_mode")
ALERT_SEVERITY_ENUM = _pg_enum(AlertSeverity, "alert_severity")
ALERT_STATUS_ENUM = _pg_enum(AlertStatus, "alert_status")
SCENARIO_TYPE_ENUM = _pg_enum(ScenarioType, "scenario_type")
RECOMMENDATION_CATEGORY_ENUM = _pg_enum(
    RecommendationCategory, "recommendation_category"
)
URGENCY_ENUM = _pg_enum(Urgency, "urgency")
