"""MODELLED station energy state tables.

Every table in this module holds RESEARCH-BASED MODEL output. None of it is
telemetry from a real Antarctic station.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import (
    BATTERY_MODE_ENUM,
    GENERATOR_STATUS_ENUM,
    PROVENANCE_ENUM,
    BatteryMode,
    DataProvenance,
    GeneratorStatus,
    TimestampMixin,
)


class EnergyParameter(Base, TimestampMixin):
    """Time-series snapshot of the whole modelled energy balance.

    One row per hour: what the station consumed, generated and stored.
    """

    __tablename__ = "energy_parameters"
    __table_args__ = (
        UniqueConstraint("station_id", "recorded_at", name="uq_energy_station_ts"),
        Index("ix_energy_station_ts", "station_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    weather_id: Mapped[int | None] = mapped_column(
        ForeignKey("weather_observations.id", ondelete="SET NULL")
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # --- demand (MODELLED) ---
    total_load_kw: Mapped[float] = mapped_column(Float, nullable=False)
    critical_load_kw: Mapped[float] = mapped_column(Float, nullable=False)
    deferrable_load_kw: Mapped[float] = mapped_column(Float, default=0.0)
    shed_load_kw: Mapped[float] = mapped_column(Float, default=0.0)
    unserved_load_kw: Mapped[float] = mapped_column(Float, default=0.0)

    # --- generation (MODELLED, driven by REAL weather) ---
    wind_generation_kw: Mapped[float] = mapped_column(Float, default=0.0)
    solar_generation_kw: Mapped[float] = mapped_column(Float, default=0.0)
    generator_output_kw: Mapped[float] = mapped_column(Float, default=0.0)
    renewable_curtailed_kw: Mapped[float] = mapped_column(Float, default=0.0)

    # --- storage flow (MODELLED) ---
    battery_charge_kw: Mapped[float] = mapped_column(Float, default=0.0)
    battery_discharge_kw: Mapped[float] = mapped_column(Float, default=0.0)
    battery_soc_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # --- fuel + derived KPIs (MODELLED) ---
    fuel_consumed_l: Mapped[float] = mapped_column(Float, default=0.0)
    fuel_level_l: Mapped[float] = mapped_column(Float, default=0.0)
    renewable_fraction: Mapped[float] = mapped_column(Float, default=0.0)
    energy_autonomy_hours: Mapped[float] = mapped_column(Float, default=0.0)
    co2_kg: Mapped[float] = mapped_column(Float, default=0.0)

    #: The measured weather that drove this modelled hour. Stored on the row
    #: so it is self-describing: reading it back never risks pairing an old
    #: modelled hour with the current live observation.
    temperature_c: Mapped[float | None] = mapped_column(Float)
    wind_speed_ms: Mapped[float | None] = mapped_column(Float)

    is_anomaly: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    anomaly_score: Mapped[float | None] = mapped_column(Float)

    provenance: Mapped[DataProvenance] = mapped_column(
        PROVENANCE_ENUM,
        default=DataProvenance.MODELLED,
        nullable=False,
    )

    station = relationship("Station", back_populates="energy_parameters")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Energy {self.recorded_at:%Y-%m-%d %H:%M} "
            f"load={self.total_load_kw:.1f}kW soc={self.battery_soc_pct:.0f}%>"
        )


class LoadProfile(Base, TimestampMixin):
    """Per-channel breakdown of the modelled station load."""

    __tablename__ = "load_profiles"
    __table_args__ = (
        UniqueConstraint(
            "station_id", "recorded_at", "channel_key", name="uq_load_station_ts_ch"
        ),
        Index("ix_load_station_ts", "station_id", "recorded_at"),
        Index("ix_load_priority", "station_id", "priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    channel_key: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_label: Mapped[str] = mapped_column(String(160), nullable=False)
    priority: Mapped[str] = mapped_column(String(32), nullable=False)

    demand_kw: Mapped[float] = mapped_column(Float, nullable=False)
    served_kw: Mapped[float] = mapped_column(Float, nullable=False)
    shed_kw: Mapped[float] = mapped_column(Float, default=0.0)
    deferred_kw: Mapped[float] = mapped_column(Float, default=0.0)
    is_deferrable: Mapped[bool] = mapped_column(Boolean, default=False)

    provenance: Mapped[DataProvenance] = mapped_column(
        PROVENANCE_ENUM,
        default=DataProvenance.MODELLED,
        nullable=False,
    )

    station = relationship("Station", back_populates="load_profiles")


class BatteryState(Base, TimestampMixin):
    """Modelled battery-bank state estimate."""

    __tablename__ = "battery_states"
    __table_args__ = (
        UniqueConstraint("station_id", "recorded_at", name="uq_batt_station_ts"),
        Index("ix_batt_station_ts", "station_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    soc_pct: Mapped[float] = mapped_column(Float, nullable=False)
    stored_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    usable_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    nominal_capacity_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    effective_capacity_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    power_kw: Mapped[float] = mapped_column(Float, default=0.0)  # +chg / -dis
    mode: Mapped[BatteryMode] = mapped_column(
        BATTERY_MODE_ENUM,
        default=BatteryMode.IDLE,
        nullable=False,
    )
    temperature_c: Mapped[float | None] = mapped_column(Float)
    temp_derate_factor: Mapped[float] = mapped_column(Float, default=1.0)
    state_of_health_pct: Mapped[float] = mapped_column(Float, default=100.0)
    cycles_equivalent: Mapped[float] = mapped_column(Float, default=0.0)
    hours_to_reserve: Mapped[float | None] = mapped_column(Float)

    provenance: Mapped[DataProvenance] = mapped_column(
        PROVENANCE_ENUM,
        default=DataProvenance.MODELLED,
        nullable=False,
    )

    station = relationship("Station", back_populates="battery_states")


class GeneratorState(Base, TimestampMixin):
    """Modelled diesel genset state, one row per unit per hour."""

    __tablename__ = "generator_states"
    __table_args__ = (
        UniqueConstraint(
            "station_id", "recorded_at", "unit_id", name="uq_gen_station_ts_unit"
        ),
        Index("ix_gen_station_ts", "station_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    unit_id: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_label: Mapped[str] = mapped_column(String(48), default="DG")
    status: Mapped[GeneratorStatus] = mapped_column(
        GENERATOR_STATUS_ENUM,
        default=GeneratorStatus.OFFLINE,
        nullable=False,
        index=True,
    )
    output_kw: Mapped[float] = mapped_column(Float, default=0.0)
    rated_kw: Mapped[float] = mapped_column(Float, nullable=False)
    loading_pct: Mapped[float] = mapped_column(Float, default=0.0)
    fuel_rate_lph: Mapped[float] = mapped_column(Float, default=0.0)
    specific_fuel_l_per_kwh: Mapped[float | None] = mapped_column(Float)
    efficiency_pct: Mapped[float] = mapped_column(Float, default=0.0)
    running_hours_total: Mapped[float] = mapped_column(Float, default=0.0)
    hours_to_service: Mapped[float | None] = mapped_column(Float)
    starts_today: Mapped[int] = mapped_column(Integer, default=0)
    wet_stacking_risk: Mapped[bool] = mapped_column(Boolean, default=False)

    provenance: Mapped[DataProvenance] = mapped_column(
        PROVENANCE_ENUM,
        default=DataProvenance.MODELLED,
        nullable=False,
    )

    station = relationship("Station", back_populates="generator_states")


class FuelState(Base, TimestampMixin):
    """Modelled bulk fuel inventory."""

    __tablename__ = "fuel_states"
    __table_args__ = (
        UniqueConstraint("station_id", "recorded_at", name="uq_fuel_station_ts"),
        Index("ix_fuel_station_ts", "station_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    level_l: Mapped[float] = mapped_column(Float, nullable=False)
    capacity_l: Mapped[float] = mapped_column(Float, nullable=False)
    level_pct: Mapped[float] = mapped_column(Float, nullable=False)
    consumption_rate_lph: Mapped[float] = mapped_column(Float, default=0.0)
    consumed_24h_l: Mapped[float] = mapped_column(Float, default=0.0)
    days_remaining: Mapped[float | None] = mapped_column(Float)
    days_to_resupply: Mapped[float | None] = mapped_column(Float)
    energy_content_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    below_critical_reserve: Mapped[bool] = mapped_column(Boolean, default=False)
    fuel_type: Mapped[str] = mapped_column(String(48), default="Antarctic blend (SKO)")
    notes: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)

    provenance: Mapped[DataProvenance] = mapped_column(
        PROVENANCE_ENUM,
        default=DataProvenance.MODELLED,
        nullable=False,
    )

    station = relationship("Station", back_populates="fuel_states")
