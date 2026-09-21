"""Health, dashboard summary and model/system introspection."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    APP_STARTED_AT,
    banners,
    build_survival,
    current_weather_payload,
    energy_status_payload,
    forecast_summary,
    get_station_or_404,
    latest_energy_row,
    nameplate,
    weather_dict,
)
from app.config import settings
from app.core.station import LOAD_CHANNELS, PRIORITY_LABELS, SITE, THRESHOLDS
from app.database.session import get_db, ping
from app.ml.registry import registry
from app.models import (
    Alert,
    AlertStatus,
    IngestionRun,
    OptimizationResult,
    Recommendation,
    RenewableForecast,
    Station,
    WeatherObservation,
)
from app.schemas.common import HealthResponse
from app.schemas.polaris import (
    AlertOut,
    DashboardSummary,
    RecommendationOut,
    SourceStatusOut,
)
from app.services import scheduler
from app.services.weather.ingest import build_current_conditions, source_status_rows
from app.services.weather.providers import PROVIDER_DESCRIPTIONS

log = logging.getLogger("polaris.api.system")

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)):
    """Liveness + readiness. Never raises - it is the probe of last resort."""
    db_ok, db_info = ping()
    status = "ok" if db_ok else "error"

    live_status = None
    last_update = None
    age = None
    active_source = None
    try:
        # Reuse the exact same merge + freshness logic the dashboard reads, so
        # /health and /weather/current can never disagree about staleness.
        station = db.scalar(select(Station).where(Station.code == SITE.station_code))
        if station is not None:
            cc = build_current_conditions(db, station.id)
            live_status = cc.status
            last_update = cc.observed_at
            age = cc.age_seconds
            active_source = cc.primary_source
            if cc.status != "LIVE" and status == "ok":
                status = "degraded"
        else:
            live_status = "NO_DATA"
            status = "degraded" if status == "ok" else status
    except Exception:
        log.exception("Health probe could not evaluate live weather status")
        live_status = "UNKNOWN"

    return HealthResponse(
        status=status,
        app=settings.app_name,
        version=settings.api_version,
        database_connected=db_ok,
        database_version=db_info if db_ok else None,
        database_dsn=settings.safe_dsn(),
        scheduler_running=scheduler.state.running,
        models_trained=registry.is_trained,
        live_weather_status=live_status,
        last_weather_update=last_update,
        weather_age_seconds=round(age, 1) if age is not None else None,
        active_source=active_source,
        uptime_seconds=round(
            (datetime.now(timezone.utc) - APP_STARTED_AT).total_seconds(), 1
        ),
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/dashboard/summary", response_model=DashboardSummary)
def dashboard_summary(db: Session = Depends(get_db)):
    """Everything the Overview page needs, in one round trip."""
    station = get_station_or_404(db)
    weather = current_weather_payload(db, station)

    energy = None
    survival = None
    row = latest_energy_row(db, station.id)
    if row is not None:
        energy = energy_status_payload(row)
        wx = weather_dict(weather)
        ambient = wx.get("temperature_c")
        if ambient is None:
            ambient = row.temperature_c if row.temperature_c is not None else -20.0
        survival = build_survival(
            row, ambient, weather=wx,
            forecast=forecast_summary(db, station.id),
        )

    opt = db.scalar(
        select(OptimizationResult)
        .where(OptimizationResult.station_id == station.id)
        .order_by(OptimizationResult.run_at.desc()).limit(1)
    )
    optimization = None
    if opt:
        optimization = {
            "run_at": opt.run_at.isoformat(),
            "horizon_h": opt.horizon_h,
            "baseline_fuel_l": opt.baseline_fuel_l,
            "optimized_fuel_l": opt.optimized_fuel_l,
            "fuel_saved_l": opt.fuel_saved_l,
            "fuel_saved_pct": opt.fuel_saved_pct,
            "renewable_fraction": opt.renewable_fraction,
            "co2_avoided_kg": opt.co2_avoided_kg,
            "load_deferred_kwh": opt.load_deferred_kwh,
            "feasible": opt.feasible,
            "rationale": opt.rationale or [],
        }

    top_rec = db.scalar(
        select(Recommendation)
        .where(Recommendation.station_id == station.id,
               Recommendation.is_active.is_(True))
        .order_by(Recommendation.generated_at.desc(), Recommendation.id).limit(1)
    )

    active_alerts = list(db.scalars(
        select(Alert)
        .where(Alert.station_id == station.id, Alert.status == AlertStatus.ACTIVE)
        .order_by(Alert.raised_at.desc()).limit(20)
    ))
    counts: dict[str, int] = {}
    for a in active_alerts:
        key = a.severity.value if hasattr(a.severity, "value") else str(a.severity)
        counts[key] = counts.get(key, 0) + 1

    preview = list(db.scalars(
        select(RenewableForecast)
        .where(RenewableForecast.station_id == station.id)
        .order_by(RenewableForecast.target_time).limit(24)
    ))
    forecast_preview = [
        {
            "target_time": r.target_time.isoformat(),
            "wind_kw": r.wind_kw, "solar_kw": r.solar_kw, "total_kw": r.total_kw,
        }
        for r in preview
    ]

    last_run = db.scalar(
        select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(1)
    )

    return DashboardSummary(
        station=nameplate(),
        live_weather=weather,
        energy=energy,
        survival=survival,
        optimization=optimization,
        top_recommendation=(
            RecommendationOut.model_validate(top_rec) if top_rec else None
        ),
        active_alerts=[AlertOut.model_validate(a) for a in active_alerts],
        alert_counts=counts,
        forecast_preview=forecast_preview,
        data_sources=[SourceStatusOut.model_validate(s) for s in source_status_rows(db)],
        scheduler=scheduler.state.as_dict(),
        pipeline={
            "last_run_at": last_run.started_at.isoformat() if last_run else None,
            "succeeded": last_run.succeeded if last_run else None,
            "observations_written": last_run.observations_written if last_run else 0,
            "error": last_run.error if last_run else None,
            "models_trained": registry.is_trained,
            "trained_at": (
                registry.trained_at.isoformat() if registry.trained_at else None
            ),
        },
        banners=banners(),
    )


@router.get("/model/info")
def model_info(db: Session = Depends(get_db)):
    """Everything the Data & Model page needs: provenance, models, coverage."""
    station = get_station_or_404(db)

    total_obs = db.scalar(
        select(func.count(WeatherObservation.id))
        .where(WeatherObservation.station_id == station.id)
    ) or 0
    by_provider = db.execute(
        select(WeatherObservation.source_provider,
               func.count(WeatherObservation.id),
               func.min(WeatherObservation.observed_at),
               func.max(WeatherObservation.observed_at))
        .where(WeatherObservation.station_id == station.id)
        .group_by(WeatherObservation.source_provider)
    ).all()

    runs = list(db.scalars(
        select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(15)
    ))

    return {
        "station": nameplate(),
        "banners": banners(),
        "thresholds": {
            "autonomy_critical_h": THRESHOLDS.autonomy_critical_h,
            "autonomy_warning_h": THRESHOLDS.autonomy_warning_h,
            "soc_critical_pct": THRESHOLDS.soc_critical_pct,
            "soc_warning_pct": THRESHOLDS.soc_warning_pct,
            "storm_wind_ms": THRESHOLDS.storm_wind_ms,
            "extreme_cold_c": THRESHOLDS.extreme_cold_c,
            "renewable_fraction_target": THRESHOLDS.renewable_fraction_target,
        },
        "load_channels": [
            {
                "key": c.key, "label": c.label, "priority": c.priority.value,
                "priority_label": PRIORITY_LABELS[c.priority],
                "base_kw": c.base_kw, "diurnal_kw": c.diurnal_kw,
                "hdd_kw_per_c": c.hdd_kw_per_c, "deferrable": c.deferrable,
                "shed_fraction_max": c.shed_fraction_max,
                "description": c.description,
            }
            for c in LOAD_CHANNELS
        ],
        "providers": PROVIDER_DESCRIPTIONS,
        "data_coverage": {
            "total_observations": total_obs,
            "by_provider": [
                {
                    "provider": p, "count": c,
                    "first": f.isoformat() if f else None,
                    "last": l.isoformat() if l else None,
                }
                for p, c, f, l in by_provider
            ],
        },
        "models": registry.status(),
        "recent_ingestion_runs": [
            {
                "started_at": r.started_at.isoformat(),
                "trigger": r.trigger, "provider_key": r.provider_key,
                "succeeded": r.succeeded, "written": r.observations_written,
                "duration_ms": r.duration_ms, "error": r.error,
            }
            for r in runs
        ],
        "scheduler": scheduler.state.as_dict(),
    }


@router.post("/model/retrain")
def retrain(db: Session = Depends(get_db)):
    """Force a retrain on the stored real weather history."""
    from app.services.pipeline import load_weather_frame

    station = get_station_or_404(db)
    history = load_weather_frame(db, station.id, days=settings.history_days)
    if history.empty:
        raise HTTPException(
            status_code=404,
            detail="No real weather history to train on. Refresh live data first.",
        )
    status = registry.train(history, force=True)
    return {"ok": registry.is_trained, "rows": len(history), **status}


@router.get("/scheduler/status")
def scheduler_status():
    return scheduler.state.as_dict()
