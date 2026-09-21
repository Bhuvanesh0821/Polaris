"""Decision-layer tables: optimization, crisis simulation, recommendations, alerts."""

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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import (
    ALERT_SEVERITY_ENUM,
    ALERT_STATUS_ENUM,
    PROVENANCE_ENUM,
    RECOMMENDATION_CATEGORY_ENUM,
    SCENARIO_TYPE_ENUM,
    URGENCY_ENUM,
    AlertSeverity,
    AlertStatus,
    DataProvenance,
    RecommendationCategory,
    ScenarioType,
    TimestampMixin,
    Urgency,
)


class OptimizationResult(Base, TimestampMixin):
    """One run of the energy optimiser over a rolling horizon."""

    __tablename__ = "optimization_results"
    __table_args__ = (Index("ix_opt_station_run", "station_id", "run_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    horizon_h: Mapped[int] = mapped_column(Integer, default=48)
    objective: Mapped[str] = mapped_column(String(48), default="min_fuel_secure_load")
    strategy: Mapped[str] = mapped_column(String(48), default="receding_horizon")

    # --- headline results ---
    baseline_fuel_l: Mapped[float] = mapped_column(Float, default=0.0)
    optimized_fuel_l: Mapped[float] = mapped_column(Float, default=0.0)
    fuel_saved_l: Mapped[float] = mapped_column(Float, default=0.0)
    fuel_saved_pct: Mapped[float] = mapped_column(Float, default=0.0)
    renewable_fraction: Mapped[float] = mapped_column(Float, default=0.0)
    renewable_curtailed_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    generator_runtime_h: Mapped[float] = mapped_column(Float, default=0.0)
    generator_starts: Mapped[int] = mapped_column(Integer, default=0)
    load_shed_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    load_deferred_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    unserved_critical_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    battery_throughput_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    final_soc_pct: Mapped[float] = mapped_column(Float, default=0.0)
    co2_avoided_kg: Mapped[float] = mapped_column(Float, default=0.0)

    feasible: Mapped[bool] = mapped_column(Boolean, default=True)
    solve_ms: Mapped[float | None] = mapped_column(Float)
    #: hour-by-hour dispatch schedule
    schedule: Mapped[list | None] = mapped_column(JSONB)
    #: human-readable justification of the chosen dispatch
    rationale: Mapped[list | None] = mapped_column(JSONB)
    constraints_binding: Mapped[list | None] = mapped_column(JSONB)

    provenance: Mapped[DataProvenance] = mapped_column(
        PROVENANCE_ENUM, default=DataProvenance.OPTIMIZED, nullable=False
    )

    station = relationship("Station", back_populates="optimization_results")


class CrisisSimulation(Base, TimestampMixin):
    """A what-if scenario. Never live telemetry."""

    __tablename__ = "crisis_simulations"
    __table_args__ = (Index("ix_crisis_station_run", "station_id", "run_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    scenario: Mapped[ScenarioType] = mapped_column(
        SCENARIO_TYPE_ENUM, nullable=False, index=True
    )
    scenario_label: Mapped[str] = mapped_column(String(120), nullable=False)
    duration_h: Mapped[int] = mapped_column(Integer, default=72)
    severity: Mapped[float] = mapped_column(Float, default=1.0)
    parameters: Mapped[dict | None] = mapped_column(JSONB)

    # --- outcome ---
    survival_hours: Mapped[float] = mapped_column(Float, default=0.0)
    baseline_survival_hours: Mapped[float] = mapped_column(Float, default=0.0)
    survival_delta_hours: Mapped[float] = mapped_column(Float, default=0.0)
    min_soc_pct: Mapped[float] = mapped_column(Float, default=0.0)
    fuel_used_l: Mapped[float] = mapped_column(Float, default=0.0)
    fuel_remaining_l: Mapped[float] = mapped_column(Float, default=0.0)
    load_shed_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    unserved_critical_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    critical_load_secured: Mapped[bool] = mapped_column(Boolean, default=True)
    blackout_occurred: Mapped[bool] = mapped_column(Boolean, default=False)
    time_to_first_shed_h: Mapped[float | None] = mapped_column(Float)
    severity_rating: Mapped[str | None] = mapped_column(String(32))

    timeline: Mapped[list | None] = mapped_column(JSONB)
    actions_taken: Mapped[list | None] = mapped_column(JSONB)
    summary: Mapped[str | None] = mapped_column(Text)
    #: constant reminder attached to every row
    disclaimer: Mapped[str] = mapped_column(
        String(200), default="SIMULATED SCENARIO - NOT LIVE STATION TELEMETRY"
    )

    provenance: Mapped[DataProvenance] = mapped_column(
        PROVENANCE_ENUM, default=DataProvenance.SIMULATED, nullable=False
    )

    station = relationship("Station", back_populates="crisis_simulations")


class Recommendation(Base, TimestampMixin):
    """Explainable AI decision output."""

    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_rec_station_created", "station_id", "generated_at"),
        Index("ix_rec_active", "station_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    category: Mapped[RecommendationCategory] = mapped_column(
        RECOMMENDATION_CATEGORY_ENUM, nullable=False, index=True
    )
    urgency: Mapped[Urgency] = mapped_column(
        URGENCY_ENUM, default=Urgency.ROUTINE, nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    # --- explainability payload ---
    #: ranked drivers behind this recommendation
    drivers: Mapped[list | None] = mapped_column(JSONB)
    #: "WHY THIS ACTION?" - plain statements derived from live model values
    reasons: Mapped[list | None] = mapped_column(JSONB)
    expected_benefit: Mapped[str | None] = mapped_column(Text)
    estimated_fuel_saving_l: Mapped[float | None] = mapped_column(Float)
    estimated_autonomy_gain_h: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    counterfactual: Mapped[str | None] = mapped_column(Text)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    acted_on: Mapped[bool] = mapped_column(Boolean, default=False)
    source_module: Mapped[str | None] = mapped_column(String(64))

    provenance: Mapped[DataProvenance] = mapped_column(
        PROVENANCE_ENUM, default=DataProvenance.ML_PREDICTED, nullable=False
    )

    station = relationship("Station", back_populates="recommendations")


class Alert(Base, TimestampMixin):
    """Threshold / anomaly alert raised against the modelled energy state."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alert_station_raised", "station_id", "raised_at"),
        Index("ix_alert_status", "station_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    raised_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    code: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    severity: Mapped[AlertSeverity] = mapped_column(
        ALERT_SEVERITY_ENUM, nullable=False, index=True
    )
    status: Mapped[AlertStatus] = mapped_column(
        ALERT_STATUS_ENUM, default=AlertStatus.ACTIVE, nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    subsystem: Mapped[str | None] = mapped_column(String(48))

    metric_name: Mapped[str | None] = mapped_column(String(64))
    metric_value: Mapped[float | None] = mapped_column(Float)
    threshold_value: Mapped[float | None] = mapped_column(Float)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)

    provenance: Mapped[DataProvenance] = mapped_column(
        PROVENANCE_ENUM, default=DataProvenance.MODELLED, nullable=False
    )

    station = relationship("Station", back_populates="alerts")
