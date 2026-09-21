"""Health and audit trail for the LIVE data pipeline.

Every attempt POLARIS makes to reach an external weather provider is recorded
here. The dashboard's LIVE / STALE / FAILED indicator, the "last updated"
timestamp and the "data source" label are all driven by these rows - never by
a hardcoded value.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base
from app.models.base import SOURCE_HEALTH_ENUM, SourceHealth, TimestampMixin


class DataSourceStatus(Base, TimestampMixin):
    """Current rolling health of one external provider. One row per provider."""

    __tablename__ = "data_source_status"
    __table_args__ = (
        UniqueConstraint("provider_key", name="uq_source_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_key: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    provider_label: Mapped[str] = mapped_column(String(160), nullable=False)
    #: Text, not VARCHAR: a fully-expanded provider query string runs to
    #: several hundred characters and must not be silently truncated.
    endpoint: Mapped[str | None] = mapped_column(Text)
    #: lower number = tried first
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: True for the provider that produced the most recent successful fetch
    is_active_source: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    health: Mapped[SourceHealth] = mapped_column(
        SOURCE_HEALTH_ENUM, default=SourceHealth.UNKNOWN, nullable=False, index=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    last_latency_ms: Mapped[float | None] = mapped_column(Float)

    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    total_attempts: Mapped[int] = mapped_column(Integer, default=0)
    total_successes: Mapped[int] = mapped_column(Integer, default=0)
    total_observations: Mapped[int] = mapped_column(Integer, default=0)
    #: seconds after which data from this provider counts as stale
    freshness_window_s: Mapped[int] = mapped_column(Integer, default=3600)

    supports_solar_radiation: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DataSourceStatus {self.provider_key} {self.health}>"


class IngestionRun(Base):
    """Append-only log of individual refresh cycles (for the Data & Model page)."""

    __tablename__ = "ingestion_runs"
    __table_args__ = (Index("ix_ingest_started", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float | None] = mapped_column(Float)

    trigger: Mapped[str] = mapped_column(String(32), default="scheduled")  # or manual
    provider_key: Mapped[str | None] = mapped_column(String(48), index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    observations_written: Mapped[int] = mapped_column(Integer, default=0)
    observations_skipped: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text)
    pipeline_ran: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)
