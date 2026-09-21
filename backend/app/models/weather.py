"""weather_observations - the REAL / LIVE-DATA table of POLARIS.

Rows here are only ever written from a genuine external provider response.
POLARIS never synthesises a weather row. Each row keeps the untouched
provider payload in `raw_payload` so any displayed number can be traced
back to the bytes the source actually returned.
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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import (
    PROVENANCE_ENUM,
    REAL_WEATHER_PROVENANCE,
    DataProvenance,
    TimestampMixin,
)


class WeatherObservation(Base, TimestampMixin):
    """Hourly Antarctic environmental observation.

    PROVENANCE: REAL_OBSERVED / REAL_FORECAST rows come from a public
    meteorological archive (ERA5 reanalysis via Open-Meteo) for the station's
    real coordinates. CLIMATOLOGY rows are a statistical fallback used when
    the network is unavailable and are explicitly *not* observations.
    """

    __tablename__ = "weather_observations"
    __table_args__ = (
        UniqueConstraint("station_id", "observed_at", name="uq_weather_station_ts"),
        Index("ix_weather_station_ts_desc", "station_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # --- measured variables (REAL) ---
    temperature_c: Mapped[float] = mapped_column(Float, nullable=False)
    apparent_temperature_c: Mapped[float | None] = mapped_column(Float)
    wind_speed_ms: Mapped[float] = mapped_column(Float, nullable=False)
    wind_gust_ms: Mapped[float | None] = mapped_column(Float)
    wind_direction_deg: Mapped[float | None] = mapped_column(Float)
    #: NULL when the reporting source cannot measure it. A WMO SYNOP bulletin
    #: carries no radiation group, so POLARIS leaves this empty rather than
    #: inventing a value, and sources it separately where available.
    solar_radiation_wm2: Mapped[float | None] = mapped_column(Float)
    direct_radiation_wm2: Mapped[float | None] = mapped_column(Float)
    diffuse_radiation_wm2: Mapped[float | None] = mapped_column(Float)
    humidity_pct: Mapped[float | None] = mapped_column(Float)
    pressure_hpa: Mapped[float | None] = mapped_column(Float)
    cloud_cover_pct: Mapped[float | None] = mapped_column(Float)
    snowfall_mm: Mapped[float | None] = mapped_column(Float)

    # --- derived (computed by POLARIS, not measured) ---
    wind_chill_c: Mapped[float | None] = mapped_column(Float)
    air_density_kg_m3: Mapped[float | None] = mapped_column(Float)
    is_polar_night: Mapped[bool] = mapped_column(Boolean, default=False)
    is_blizzard: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- provenance / live-pipeline bookkeeping -----------------------------
    provenance: Mapped[DataProvenance] = mapped_column(
        PROVENANCE_ENUM,
        default=DataProvenance.LIVE_OBSERVED,
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    source_provider: Mapped[str] = mapped_column(String(48), nullable=False)
    source_station_code: Mapped[str | None] = mapped_column(String(32))
    #: when POLARIS retrieved the row (distinct from when it was observed)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    #: untouched provider response for this observation - full audit trail
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)

    station = relationship("Station", back_populates="weather_observations")

    @property
    def is_real(self) -> bool:
        return self.provenance in REAL_WEATHER_PROVENANCE

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Weather {self.observed_at:%Y-%m-%d %H:%M} "
            f"{self.temperature_c:.1f}C {self.wind_speed_ms:.1f}m/s>"
        )
