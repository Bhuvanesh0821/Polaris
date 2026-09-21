"""Parent entity every time-series table hangs off."""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base
from app.models.base import STATION_KIND_ENUM, StationKind, TimestampMixin


class Station(Base, TimestampMixin):
    """A polar research station.

    Geographic fields are REAL. Plant nameplate fields are MODELLED - see
    app/core/station.py for the provenance note.
    """

    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    operator: Mapped[str | None] = mapped_column(String(160))
    #: MODELLED_PLANT carries the POLARIS energy model; REAL_OBSERVATION rows
    #: are genuine NOAA-reporting Antarctic stations used as reference data.
    kind: Mapped[StationKind] = mapped_column(
        STATION_KIND_ENUM, default=StationKind.MODELLED_PLANT, nullable=False, index=True
    )
    #: provider-specific identifier, e.g. the ICAO code for a METAR station
    source_station_code: Mapped[str | None] = mapped_column(String(32), index=True)

    # --- REAL geography ---
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_m: Mapped[float] = mapped_column(Float, default=0.0)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")

    # --- MODELLED plant nameplate ---
    wind_rated_kw: Mapped[float] = mapped_column(Float, default=0.0)
    solar_rated_kwp: Mapped[float] = mapped_column(Float, default=0.0)
    battery_nominal_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    generator_rated_kw: Mapped[float] = mapped_column(Float, default=0.0)
    fuel_capacity_l: Mapped[float] = mapped_column(Float, default=0.0)
    summer_crew: Mapped[int] = mapped_column(Integer, default=0)
    winter_crew: Mapped[int] = mapped_column(Integer, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    # --- relationships ---
    weather_observations = relationship(
        "WeatherObservation", back_populates="station", cascade="all, delete-orphan"
    )
    energy_parameters = relationship(
        "EnergyParameter", back_populates="station", cascade="all, delete-orphan"
    )
    load_profiles = relationship(
        "LoadProfile", back_populates="station", cascade="all, delete-orphan"
    )
    battery_states = relationship(
        "BatteryState", back_populates="station", cascade="all, delete-orphan"
    )
    generator_states = relationship(
        "GeneratorState", back_populates="station", cascade="all, delete-orphan"
    )
    fuel_states = relationship(
        "FuelState", back_populates="station", cascade="all, delete-orphan"
    )
    renewable_forecasts = relationship(
        "RenewableForecast", back_populates="station", cascade="all, delete-orphan"
    )
    energy_forecasts = relationship(
        "EnergyForecast", back_populates="station", cascade="all, delete-orphan"
    )
    optimization_results = relationship(
        "OptimizationResult", back_populates="station", cascade="all, delete-orphan"
    )
    crisis_simulations = relationship(
        "CrisisSimulation", back_populates="station", cascade="all, delete-orphan"
    )
    recommendations = relationship(
        "Recommendation", back_populates="station", cascade="all, delete-orphan"
    )
    alerts = relationship(
        "Alert", back_populates="station", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Station {self.code} {self.latitude:.3f},{self.longitude:.3f}>"
