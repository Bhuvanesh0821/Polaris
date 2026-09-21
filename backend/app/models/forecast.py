"""AI forecast tables - renewable_forecasts and energy_forecasts.

Both are ML_PREDICTED. They are produced by scikit-learn models whose INPUTS
are the live/real weather rows, and whose TARGETS were learned from the
research-based energy model. Predictions are therefore labelled AI FORECAST,
never live telemetry.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import PROVENANCE_ENUM, DataProvenance, TimestampMixin


class RenewableForecast(Base, TimestampMixin):
    """Predicted wind + solar output for a future hour."""

    __tablename__ = "renewable_forecasts"
    __table_args__ = (
        UniqueConstraint(
            "station_id", "run_at", "target_time", name="uq_renew_run_target"
        ),
        Index("ix_renew_station_target", "station_id", "target_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    #: when the forecast was generated
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    #: the hour being forecast
    target_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    horizon_h: Mapped[int] = mapped_column(Integer, nullable=False)

    wind_kw: Mapped[float] = mapped_column(Float, default=0.0)
    solar_kw: Mapped[float] = mapped_column(Float, default=0.0)
    total_kw: Mapped[float] = mapped_column(Float, default=0.0)
    wind_kw_p10: Mapped[float | None] = mapped_column(Float)
    wind_kw_p90: Mapped[float | None] = mapped_column(Float)
    solar_kw_p10: Mapped[float | None] = mapped_column(Float)
    solar_kw_p90: Mapped[float | None] = mapped_column(Float)

    # physical baseline vs ML-corrected, kept apart for explainability
    wind_physical_kw: Mapped[float | None] = mapped_column(Float)
    solar_physical_kw: Mapped[float | None] = mapped_column(Float)
    ml_correction_kw: Mapped[float | None] = mapped_column(Float)

    # weather drivers this prediction was based on (REAL forecast input)
    input_temperature_c: Mapped[float | None] = mapped_column(Float)
    input_wind_speed_ms: Mapped[float | None] = mapped_column(Float)
    input_solar_radiation_wm2: Mapped[float | None] = mapped_column(Float)
    weather_provenance: Mapped[str | None] = mapped_column(String(32))
    weather_source: Mapped[str | None] = mapped_column(String(120))

    turbine_curtailed: Mapped[bool | None] = mapped_column()
    icing_risk: Mapped[bool | None] = mapped_column()
    model_version: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column(Float)

    provenance: Mapped[DataProvenance] = mapped_column(
        PROVENANCE_ENUM, default=DataProvenance.ML_PREDICTED, nullable=False
    )

    station = relationship("Station", back_populates="renewable_forecasts")


class EnergyForecast(Base, TimestampMixin):
    """Predicted station load / demand for a future hour."""

    __tablename__ = "energy_forecasts"
    __table_args__ = (
        UniqueConstraint(
            "station_id", "run_at", "target_time", name="uq_efc_run_target"
        ),
        Index("ix_efc_station_target", "station_id", "target_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    target_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    horizon_h: Mapped[int] = mapped_column(Integer, nullable=False)

    predicted_load_kw: Mapped[float] = mapped_column(Float, nullable=False)
    load_kw_p10: Mapped[float | None] = mapped_column(Float)
    load_kw_p90: Mapped[float | None] = mapped_column(Float)
    critical_load_kw: Mapped[float] = mapped_column(Float, default=0.0)
    deferrable_load_kw: Mapped[float] = mapped_column(Float, default=0.0)
    heating_load_kw: Mapped[float | None] = mapped_column(Float)
    predicted_energy_kwh: Mapped[float | None] = mapped_column(Float)

    input_temperature_c: Mapped[float | None] = mapped_column(Float)
    input_wind_speed_ms: Mapped[float | None] = mapped_column(Float)
    weather_provenance: Mapped[str | None] = mapped_column(String(32))
    weather_source: Mapped[str | None] = mapped_column(String(120))

    model_version: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column(Float)
    feature_snapshot: Mapped[dict | None] = mapped_column(JSONB)

    provenance: Mapped[DataProvenance] = mapped_column(
        PROVENANCE_ENUM, default=DataProvenance.ML_PREDICTED, nullable=False
    )

    station = relationship("Station", back_populates="energy_forecasts")
