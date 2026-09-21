"""Energy status, history and AI forecast endpoints (MODELLED / AI FORECAST)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    build_survival,
    current_weather_payload,
    energy_status_payload,
    forecast_summary,
    get_station_or_404,
    latest_energy_row,
    weather_dict,
)
from app.database.session import get_db
from app.models import (
    EnergyForecast,
    EnergyParameter,
    LoadProfile,
    RenewableForecast,
)
from app.schemas.common import (
    PROVENANCE_FORECAST,
    PROVENANCE_MODELLED,
    Envelope,
)
from app.schemas.polaris import (
    EnergyHistoryPoint,
    EnergyStatusOut,
    LoadChannelOut,
    LoadForecastPoint,
    RenewableForecastPoint,
    SurvivalOut,
)

router = APIRouter(tags=["energy (MODELLED)"])

_NO_DATA = (
    "No modelled energy state yet. The model runs after the first successful "
    "live weather refresh - call POST /api/weather/refresh."
)


@router.get("/energy/status", response_model=Envelope[EnergyStatusOut])
def energy_status(db: Session = Depends(get_db)):
    """Current MODELLED energy state, estimated from live weather."""
    station = get_station_or_404(db)
    row = latest_energy_row(db, station.id)
    if row is None:
        raise HTTPException(status_code=404, detail=_NO_DATA)

    # The payload reports the weather that drove this modelled hour, taken
    # from the row itself. Live current conditions are a separate endpoint.
    payload = energy_status_payload(row)

    return Envelope(
        data=payload,
        provenance=PROVENANCE_MODELLED,
        generated_at=datetime.now(timezone.utc),
        notice=(
            "Battery, fuel, generator and load values are MODEL ESTIMATES "
            "driven by live weather - not station telemetry."
        ),
    )


@router.get("/energy/history", response_model=Envelope[list[EnergyHistoryPoint]])
def energy_history(
    hours: int = Query(72, ge=1, le=24 * 30),
    db: Session = Depends(get_db),
):
    station = get_station_or_404(db)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = list(db.scalars(
        select(EnergyParameter)
        .where(
            EnergyParameter.station_id == station.id,
            EnergyParameter.recorded_at >= since,
        )
        .order_by(EnergyParameter.recorded_at)
    ))
    return Envelope(
        data=[
            EnergyHistoryPoint(
                recorded_at=r.recorded_at,
                total_load_kw=r.total_load_kw,
                critical_load_kw=r.critical_load_kw,
                wind_generation_kw=r.wind_generation_kw,
                solar_generation_kw=r.solar_generation_kw,
                generator_output_kw=r.generator_output_kw,
                battery_soc_pct=r.battery_soc_pct,
                fuel_level_l=r.fuel_level_l,
                renewable_fraction=r.renewable_fraction,
                shed_load_kw=r.shed_load_kw,
            )
            for r in rows
        ],
        provenance=PROVENANCE_MODELLED,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/energy/load-profile", response_model=Envelope[list[LoadChannelOut]])
def load_profile(db: Session = Depends(get_db)):
    """Per-circuit demand breakdown for the most recent modelled hour."""
    station = get_station_or_404(db)
    latest_ts = db.scalar(
        select(LoadProfile.recorded_at)
        .where(LoadProfile.station_id == station.id)
        .order_by(LoadProfile.recorded_at.desc())
        .limit(1)
    )
    if latest_ts is None:
        raise HTTPException(status_code=404, detail=_NO_DATA)

    rows = list(db.scalars(
        select(LoadProfile).where(
            LoadProfile.station_id == station.id,
            LoadProfile.recorded_at == latest_ts,
        )
    ))
    rows.sort(key=lambda r: (r.priority, -r.demand_kw))
    return Envelope(
        data=[
            LoadChannelOut(
                channel_key=r.channel_key, channel_label=r.channel_label,
                priority=r.priority, demand_kw=r.demand_kw,
                is_deferrable=r.is_deferrable,
            )
            for r in rows
        ],
        provenance=PROVENANCE_MODELLED,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/load/forecast", response_model=Envelope[list[LoadForecastPoint]])
def load_forecast(
    hours: int = Query(48, ge=1, le=168),
    db: Session = Depends(get_db),
):
    """AI FORECAST of station demand, driven by the real weather forecast."""
    station = get_station_or_404(db)
    rows = list(db.scalars(
        select(EnergyForecast)
        .where(EnergyForecast.station_id == station.id)
        .order_by(EnergyForecast.target_time)
        .limit(hours)
    ))
    if not rows:
        raise HTTPException(status_code=404, detail=_NO_DATA)

    return Envelope(
        data=[
            LoadForecastPoint(
                target_time=r.target_time, horizon_h=r.horizon_h,
                predicted_load_kw=r.predicted_load_kw,
                load_kw_p10=r.load_kw_p10, load_kw_p90=r.load_kw_p90,
                critical_load_kw=r.critical_load_kw,
                deferrable_load_kw=r.deferrable_load_kw,
                input_temperature_c=r.input_temperature_c,
                input_wind_speed_ms=r.input_wind_speed_ms,
                weather_provenance=r.weather_provenance,
                model_version=r.model_version,
            )
            for r in rows
        ],
        provenance=PROVENANCE_FORECAST,
        generated_at=datetime.now(timezone.utc),
        notice="Predicted by scikit-learn from REAL forecast weather inputs.",
    )


@router.get("/renewable/forecast", response_model=Envelope[list[RenewableForecastPoint]])
def renewable_forecast(
    hours: int = Query(48, ge=1, le=168),
    db: Session = Depends(get_db),
):
    """AI FORECAST of wind + solar generation (hybrid physics/ML)."""
    station = get_station_or_404(db)
    rows = list(db.scalars(
        select(RenewableForecast)
        .where(RenewableForecast.station_id == station.id)
        .order_by(RenewableForecast.target_time)
        .limit(hours)
    ))
    if not rows:
        raise HTTPException(status_code=404, detail=_NO_DATA)

    return Envelope(
        data=[
            RenewableForecastPoint(
                target_time=r.target_time, horizon_h=r.horizon_h,
                wind_kw=r.wind_kw, solar_kw=r.solar_kw, total_kw=r.total_kw,
                wind_kw_p10=r.wind_kw_p10, wind_kw_p90=r.wind_kw_p90,
                solar_kw_p10=r.solar_kw_p10, solar_kw_p90=r.solar_kw_p90,
                wind_physical_kw=r.wind_physical_kw,
                solar_physical_kw=r.solar_physical_kw,
                ml_correction_kw=r.ml_correction_kw,
                input_wind_speed_ms=r.input_wind_speed_ms,
                input_solar_radiation_wm2=r.input_solar_radiation_wm2,
                icing_risk=r.icing_risk, turbine_curtailed=r.turbine_curtailed,
                weather_provenance=r.weather_provenance,
            )
            for r in rows
        ],
        provenance=PROVENANCE_FORECAST,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/survival-analysis", response_model=Envelope[SurvivalOut])
def survival_analysis(
    generators_available: int | None = Query(None, ge=0, le=3),
    db: Session = Depends(get_db),
):
    """Estimated energy survival time under current modelled conditions."""
    station = get_station_or_404(db)
    row = latest_energy_row(db, station.id)
    if row is None:
        raise HTTPException(status_code=404, detail=_NO_DATA)

    weather = current_weather_payload(db, station)
    wx = weather_dict(weather)
    ambient = wx.get("temperature_c")
    if ambient is None:
        ambient = row.temperature_c if row.temperature_c is not None else -20.0

    return Envelope(
        data=build_survival(
            row, ambient, generators_available,
            weather=wx, forecast=forecast_summary(db, station.id),
        ),
        provenance=PROVENANCE_MODELLED,
        generated_at=datetime.now(timezone.utc),
        notice=(
            "Autonomy is a MODELLED estimate from modelled battery and fuel "
            "state, driven by live weather."
        ),
    )
