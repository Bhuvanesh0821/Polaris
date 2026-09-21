"""Shared helpers for the POLARIS API routes."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.physics import genset_efficiency

from app.core.station import (
    BATTERY,
    DATA_BANNER,
    FUEL,
    GENERATOR,
    LIVE_WEATHER_NOTE,
    MODEL_DISCLAIMER,
    NO_TELEMETRY_NOTE,
    SITE,
    SIMULATION_DISCLAIMER,
    THRESHOLDS,
    station_nameplate,
)
from app.models import EnergyParameter, Station
from app.schemas.polaris import (
    AutonomyModesOut,
    CurrentWeather,
    EnergyStatusOut,
    FieldValue,
    RiskOut,
    SurvivalOut,
)
from app.services import risk as risk_engine
from app.services.energy_model import autonomy_modes, estimate_autonomy
from app.services.weather.ingest import CurrentConditions, build_current_conditions

APP_STARTED_AT = datetime.now(timezone.utc)


def get_station_or_404(db: Session) -> Station:
    st = db.scalar(select(Station).where(Station.code == SITE.station_code))
    if st is None:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Station {SITE.station_code} is not provisioned. "
                "Initialise the database first: python -m scripts.init_db"
            ),
        )
    return st


def current_weather_payload(db: Session, station: Station) -> CurrentWeather:
    cc: CurrentConditions = build_current_conditions(db, station.id)
    return CurrentWeather(
        station_code=station.code,
        station_name=station.name,
        latitude=station.latitude,
        longitude=station.longitude,
        status=cc.status,
        is_live=cc.is_live,
        is_stale=cc.is_stale,
        is_fresh=cc.is_fresh,
        observed_at=cc.observed_at,
        fetched_at=cc.fetched_at,
        age_seconds=cc.age_seconds,
        primary_source=cc.primary_source,
        primary_provider=cc.primary_provider,
        contributing_sources=cc.contributing_sources,
        notice=cc.notice,
        fields={
            k: FieldValue(
                value=v.value, source=v.source, provider=v.provider,
                observed_at=v.observed_at, age_seconds=v.age_seconds,
                is_measured=v.is_measured,
            )
            for k, v in cc.fields.items()
        },
    )


def latest_energy_row(db: Session, station_id: int) -> EnergyParameter | None:
    return db.scalar(
        select(EnergyParameter)
        .where(EnergyParameter.station_id == station_id)
        .order_by(EnergyParameter.recorded_at.desc())
        .limit(1)
    )


def _genset_units_and_loading(output_kw: float) -> tuple[int, float]:
    """Recover how many gensets are carrying a given output, and at what load.

    Loading must be expressed against the units actually online: 160 kW is two
    units at 80 %, not one unit at an impossible 160 %.
    """
    if output_kw <= 0:
        return 0, 0.0
    rated = GENERATOR.rated_kw_each
    units = math.ceil(output_kw / (rated * GENERATOR.max_loading_frac))
    units = max(1, min(units, GENERATOR.n_units))
    loading = output_kw / (units * rated) * 100.0
    return units, round(loading, 2)


def energy_status_payload(row: EnergyParameter) -> EnergyStatusOut:
    gen_units, gen_loading = _genset_units_and_loading(row.generator_output_kw)
    return EnergyStatusOut(
        timestamp=row.recorded_at,
        total_load_kw=row.total_load_kw,
        critical_load_kw=row.critical_load_kw,
        deferrable_load_kw=row.deferrable_load_kw,
        shed_load_kw=row.shed_load_kw,
        wind_generation_kw=row.wind_generation_kw,
        solar_generation_kw=row.solar_generation_kw,
        renewable_kw=round(row.wind_generation_kw + row.solar_generation_kw, 3),
        renewable_curtailed_kw=row.renewable_curtailed_kw,
        generator_output_kw=row.generator_output_kw,
        generators_running=gen_units,
        generator_loading_pct=gen_loading,
        generator_efficiency=(
            genset_efficiency(row.generator_output_kw / gen_units,
                              GENERATOR.rated_kw_each, row.temperature_c)
            if gen_units else 0.0
        ),
        wet_stacking=bool(
            gen_units and gen_loading < GENERATOR.min_loading_frac * 100
        ),
        battery_soc_pct=row.battery_soc_pct,
        battery_stored_kwh=round(
            BATTERY.nominal_capacity_kwh * row.battery_soc_pct / 100.0, 2
        ),
        battery_charge_kw=row.battery_charge_kw,
        battery_discharge_kw=row.battery_discharge_kw,
        battery_mode=(
            "CHARGING" if row.battery_charge_kw > 0.1
            else "DISCHARGING" if row.battery_discharge_kw > 0.1 else "IDLE"
        ),
        fuel_level_l=row.fuel_level_l,
        fuel_level_pct=round(row.fuel_level_l / FUEL.tank_capacity_l * 100, 2),
        fuel_consumed_l=row.fuel_consumed_l,
        renewable_fraction=row.renewable_fraction,
        co2_kg=row.co2_kg,
        # The weather that actually drove this modelled hour - not the current
        # live reading, which may be a different hour entirely.
        temperature_c=row.temperature_c if row.temperature_c is not None else 0.0,
        wind_speed_ms=row.wind_speed_ms if row.wind_speed_ms is not None else 0.0,
    )


def build_survival(row: EnergyParameter, ambient_c: float,
                   generators_available: int | None = None,
                   weather: dict | None = None,
                   forecast: dict | None = None) -> SurvivalOut:
    """Full survival/autonomy analysis from a modelled energy row."""
    n_gen = GENERATOR.n_units if generators_available is None else generators_available
    renewable = row.wind_generation_kw + row.solar_generation_kw

    est = estimate_autonomy(
        critical_demand_kw=row.critical_load_kw,
        soc_pct=row.battery_soc_pct,
        fuel_l=row.fuel_level_l,
        ambient_c=ambient_c,
        renewable_kw=renewable,
        generators_available=n_gen,
    )

    modes = autonomy_modes(
        total_demand_kw=row.total_load_kw,
        critical_demand_kw=row.critical_load_kw,
        soc_pct=row.battery_soc_pct,
        fuel_l=row.fuel_level_l,
        ambient_c=ambient_c,
        renewable_kw=renewable,
        generators_available=n_gen,
    )

    gen_units, gen_loading = _genset_units_and_loading(row.generator_output_kw)
    risk = risk_engine.assess(
        energy={
            "battery_soc_pct": row.battery_soc_pct,
            "fuel_level_l": row.fuel_level_l,
            "total_load_kw": row.total_load_kw,
            "critical_load_kw": row.critical_load_kw,
            "renewable_kw": renewable,
            "shed_load_kw": row.shed_load_kw,
            "unserved_load_kw": row.unserved_load_kw,
            "fuel_consumed_l": row.fuel_consumed_l,
            "generators_running": gen_units,
        },
        autonomy_h=None if est.hours == float("inf") else est.hours,
        weather=weather or {
            "temperature_c": row.temperature_c,
            "wind_speed_ms": row.wind_speed_ms,
        },
        forecast=forecast,
    )

    infinite = est.hours == float("inf")
    hours = None if infinite else est.hours
    fuel_energy = row.fuel_level_l * FUEL.energy_kwh_per_l

    if infinite:
        status = "SECURE"
    elif est.hours < THRESHOLDS.autonomy_critical_h:
        status = "CRITICAL"
    elif est.hours < THRESHOLDS.autonomy_warning_h:
        status = "WARNING"
    elif est.hours < THRESHOLDS.autonomy_warning_h * 2:
        status = "ADEQUATE"
    else:
        status = "SECURE"

    breakdown = [
        {
            "source": "Battery bank",
            "available": round(est.battery_available_kwh, 1),
            "unit": "kWh",
            "hours": None if infinite else round(est.battery_hours, 1),
            "note": f"Above the {BATTERY.soc_min_pct:.0f}% protected floor, "
                    f"temperature-derated.",
        },
        {
            "source": "Diesel fuel",
            "available": round(est.fuel_usable_l, 0),
            "unit": "L",
            "hours": None if infinite else round(est.fuel_hours, 1),
            "note": f"Above the {FUEL.critical_level_l:,.0f} L emergency "
                    f"reserve, at the current dispatch efficiency.",
        },
        {
            "source": "Renewable generation",
            "available": round(renewable, 1),
            "unit": "kW",
            "hours": None,
            "note": "Directly offsets critical demand while conditions hold.",
        },
        {
            "source": "Generator capacity",
            "available": round(n_gen * GENERATOR.rated_kw_each, 0),
            "unit": "kW",
            "hours": None,
            "note": f"{n_gen} of {GENERATOR.n_units} units available.",
        },
    ]

    assumptions = [
        "Critical demand is held constant at the current modelled value.",
        f"Battery is not discharged below its {BATTERY.soc_min_pct:.0f}% "
        "protected floor.",
        f"A {FUEL.critical_level_l:,.0f} L emergency fuel reserve is withheld.",
        "Renewable output is assumed to persist at its current level; the "
        "crisis simulator is the tool for testing the loss of that assumption.",
        "All figures are MODELLED estimates, not station telemetry.",
    ]

    return SurvivalOut(
        estimated_autonomy_hours=hours,
        estimated_autonomy_days=None if infinite else round(est.hours / 24.0, 2),
        battery_hours=None if infinite else est.battery_hours,
        fuel_hours=None if infinite else est.fuel_hours,
        limited_by=est.limited_by,
        modes=AutonomyModesOut(**modes.as_dict()),
        risk=RiskOut(**risk.as_dict()),
        available_energy_kwh=round(est.battery_available_kwh + fuel_energy * 0.38, 1),
        battery_available_kwh=est.battery_available_kwh,
        fuel_available_l=est.fuel_available_l,
        fuel_usable_l=est.fuel_usable_l,
        fuel_energy_kwh=round(fuel_energy, 1),
        critical_demand_kw=est.critical_demand_kw,
        total_demand_kw=row.total_load_kw,
        renewable_generation_kw=round(renewable, 2),
        generators_available=n_gen,
        generator_capacity_kw=n_gen * GENERATOR.rated_kw_each,
        battery_soc_pct=row.battery_soc_pct,
        fuel_level_pct=round(row.fuel_level_l / FUEL.tank_capacity_l * 100, 2),
        status=status,
        headroom_vs_threshold_h=(
            None if infinite else round(est.hours - THRESHOLDS.autonomy_warning_h, 1)
        ),
        breakdown=breakdown,
        assumptions=assumptions,
    )


def forecast_summary(db: Session, station_id: int, hours: int = 24) -> dict | None:
    """Aggregate the AI forecast horizon for the risk engine."""
    from app.models import EnergyForecast, RenewableForecast

    loads = list(db.scalars(
        select(EnergyForecast.predicted_load_kw)
        .where(EnergyForecast.station_id == station_id)
        .order_by(EnergyForecast.target_time).limit(hours)
    ))
    renews = list(db.scalars(
        select(RenewableForecast.total_kw)
        .where(RenewableForecast.station_id == station_id)
        .order_by(RenewableForecast.target_time).limit(hours)
    ))
    if not loads or not renews:
        return None
    return {
        "hours": min(len(loads), len(renews)),
        "mean_load_kw": round(sum(loads) / len(loads), 3),
        "mean_renewable_kw": round(sum(renews) / len(renews), 3),
        "min_renewable_kw": round(min(renews), 3),
        "max_load_kw": round(max(loads), 3),
    }


def weather_dict(weather: CurrentWeather) -> dict:
    """Flatten the attributed weather view to plain measurements."""
    out: dict[str, float | None] = {}
    for key in ("temperature_c", "wind_speed_ms", "solar_radiation_wm2",
                "humidity_pct", "wind_chill_c"):
        f = weather.fields.get(key)
        out[key] = float(f.value) if f and isinstance(f.value, (int, float)) else None
    return out


def banners() -> dict[str, str]:
    return {
        "data_banner": DATA_BANNER,
        "model_disclaimer": MODEL_DISCLAIMER,
        "live_weather_note": LIVE_WEATHER_NOTE,
        "no_telemetry_note": NO_TELEMETRY_NOTE,
        "simulation_disclaimer": SIMULATION_DISCLAIMER,
    }


def nameplate() -> dict:
    return station_nameplate()
