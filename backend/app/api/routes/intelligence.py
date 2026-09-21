"""Optimization, crisis simulation, recommendations, alerts and XAI."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    _genset_units_and_loading,
    get_station_or_404,
    latest_energy_row,
)
from app.core.station import SIMULATION_DISCLAIMER
from app.database.session import get_db
from app.ml.explainability import explain_dispatch
from app.ml.features import LOAD_FEATURES, build_features, select_matrix
from app.ml.registry import registry
from app.models import (
    Alert,
    AlertStatus,
    CrisisSimulation,
    OptimizationResult,
    Recommendation,
)
from app.models.base import ScenarioType
from app.optimization.energy_optimizer import EnergyOptimizer
from app.schemas.common import (
    PROVENANCE_FORECAST,
    PROVENANCE_MODELLED,
    PROVENANCE_SIMULATED,
    Envelope,
)
from app.schemas.polaris import (
    AlertOut,
    CrisisOut,
    CrisisRequest,
    ExplainabilityOut,
    OptimizationOut,
    OptimizationRequest,
    RecommendationOut,
)
from app.services.pipeline import load_weather_frame

router = APIRouter(tags=["intelligence"])

_NO_DATA = (
    "No modelled state yet. Call POST /api/weather/refresh to pull live data "
    "and run the pipeline."
)


def _forecast_frame(db: Session, station_id: int, horizon_h: int) -> pd.DataFrame:
    """Build the optimiser input from stored AI forecasts + real weather."""
    from app.models import EnergyForecast, RenewableForecast

    loads = list(db.scalars(
        select(EnergyForecast)
        .where(EnergyForecast.station_id == station_id)
        .order_by(EnergyForecast.target_time).limit(horizon_h)
    ))
    renews = {
        r.target_time: r for r in db.scalars(
            select(RenewableForecast)
            .where(RenewableForecast.station_id == station_id)
            .order_by(RenewableForecast.target_time).limit(horizon_h)
        )
    }
    if not loads:
        return pd.DataFrame()

    rows = []
    for lf in loads:
        rf = renews.get(lf.target_time)
        rows.append({
            "target_time": lf.target_time,
            "predicted_load_kw": lf.predicted_load_kw,
            "critical_load_kw": lf.critical_load_kw,
            "deferrable_load_kw": lf.deferrable_load_kw,
            "wind_kw": rf.wind_kw if rf else 0.0,
            "solar_kw": rf.solar_kw if rf else 0.0,
            "temperature_c": lf.input_temperature_c or -20.0,
            "wind_speed_ms": lf.input_wind_speed_ms or 0.0,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Optimization
# ---------------------------------------------------------------------------


@router.post("/optimization/run", response_model=Envelope[OptimizationOut])
def run_optimization(req: OptimizationRequest, db: Session = Depends(get_db)):
    """Run the energy optimiser over the current AI forecast horizon."""
    station = get_station_or_404(db)
    row = latest_energy_row(db, station.id)
    if row is None:
        raise HTTPException(status_code=404, detail=_NO_DATA)

    forecast = _forecast_frame(db, station.id, req.horizon_h)
    if forecast.empty:
        raise HTTPException(status_code=404, detail=_NO_DATA)

    optimizer = EnergyOptimizer(reserve_soc_pct=req.reserve_soc_pct)
    outcome = optimizer.optimize(
        forecast,
        initial_soc_pct=row.battery_soc_pct,
        initial_fuel_l=row.fuel_level_l,
        horizon_h=req.horizon_h,
        allow_deferral=req.allow_deferral,
        generators_available=req.generators_available,
    )

    run_at = datetime.now(timezone.utc)
    record = OptimizationResult(
        station_id=station.id, run_at=run_at, horizon_h=req.horizon_h,
        baseline_fuel_l=outcome.baseline_fuel_l,
        optimized_fuel_l=outcome.optimized_fuel_l,
        fuel_saved_l=outcome.fuel_saved_l, fuel_saved_pct=outcome.fuel_saved_pct,
        renewable_fraction=outcome.renewable_fraction,
        renewable_curtailed_kwh=outcome.renewable_curtailed_kwh,
        generator_runtime_h=outcome.generator_runtime_h,
        generator_starts=outcome.generator_starts,
        load_shed_kwh=outcome.load_shed_kwh,
        load_deferred_kwh=outcome.load_deferred_kwh,
        unserved_critical_kwh=outcome.unserved_critical_kwh,
        battery_throughput_kwh=outcome.battery_throughput_kwh,
        final_soc_pct=outcome.final_soc_pct,
        co2_avoided_kg=outcome.co2_avoided_kg, feasible=outcome.feasible,
        solve_ms=outcome.solve_ms,
        schedule=[h.as_dict() for h in outcome.schedule],
        rationale=outcome.rationale,
        constraints_binding=outcome.constraints_binding,
    )
    db.add(record)
    db.commit()

    return Envelope(
        data=OptimizationOut(
            run_at=run_at, horizon_h=req.horizon_h,
            baseline_fuel_l=outcome.baseline_fuel_l,
            optimized_fuel_l=outcome.optimized_fuel_l,
            fuel_saved_l=outcome.fuel_saved_l,
            fuel_saved_pct=outcome.fuel_saved_pct,
            renewable_fraction=outcome.renewable_fraction,
            renewable_curtailed_kwh=outcome.renewable_curtailed_kwh,
            generator_runtime_h=outcome.generator_runtime_h,
            generator_starts=outcome.generator_starts,
            load_shed_kwh=outcome.load_shed_kwh,
            load_deferred_kwh=outcome.load_deferred_kwh,
            unserved_critical_kwh=outcome.unserved_critical_kwh,
            battery_throughput_kwh=outcome.battery_throughput_kwh,
            final_soc_pct=outcome.final_soc_pct,
            co2_avoided_kg=outcome.co2_avoided_kg,
            feasible=outcome.feasible, solve_ms=outcome.solve_ms,
            rationale=outcome.rationale,
            constraints_binding=outcome.constraints_binding,
            schedule=[h.as_dict() for h in outcome.schedule],
        ),
        provenance=PROVENANCE_MODELLED,
        generated_at=run_at,
    )


@router.get("/optimization/latest", response_model=Envelope[OptimizationOut])
def latest_optimization(db: Session = Depends(get_db)):
    station = get_station_or_404(db)
    r = db.scalar(
        select(OptimizationResult)
        .where(OptimizationResult.station_id == station.id)
        .order_by(OptimizationResult.run_at.desc()).limit(1)
    )
    if r is None:
        raise HTTPException(status_code=404, detail=_NO_DATA)
    return Envelope(
        data=OptimizationOut(
            run_at=r.run_at, horizon_h=r.horizon_h,
            baseline_fuel_l=r.baseline_fuel_l, optimized_fuel_l=r.optimized_fuel_l,
            fuel_saved_l=r.fuel_saved_l, fuel_saved_pct=r.fuel_saved_pct,
            renewable_fraction=r.renewable_fraction,
            renewable_curtailed_kwh=r.renewable_curtailed_kwh,
            generator_runtime_h=r.generator_runtime_h,
            generator_starts=r.generator_starts, load_shed_kwh=r.load_shed_kwh,
            load_deferred_kwh=r.load_deferred_kwh,
            unserved_critical_kwh=r.unserved_critical_kwh,
            battery_throughput_kwh=r.battery_throughput_kwh,
            final_soc_pct=r.final_soc_pct, co2_avoided_kg=r.co2_avoided_kg,
            feasible=r.feasible, solve_ms=r.solve_ms or 0.0,
            rationale=r.rationale or [], constraints_binding=r.constraints_binding or [],
            schedule=r.schedule or [],
        ),
        provenance=PROVENANCE_MODELLED,
        generated_at=r.run_at,
    )


# ---------------------------------------------------------------------------
# Crisis simulator
# ---------------------------------------------------------------------------


@router.get("/crisis/scenarios")
def crisis_scenarios():
    from app.simulation.crisis_simulator import CrisisSimulator

    return {
        "scenarios": CrisisSimulator.list_scenarios(),
        "disclaimer": SIMULATION_DISCLAIMER,
    }


@router.post("/crisis/simulate", response_model=Envelope[CrisisOut])
def simulate_crisis(req: CrisisRequest, db: Session = Depends(get_db)):
    """Run a what-if scenario. Output is never written to the live tables."""
    from app.simulation.crisis_simulator import CrisisSimulator, ScenarioOverrides

    station = get_station_or_404(db)
    row = latest_energy_row(db, station.id)
    if row is None:
        raise HTTPException(status_code=404, detail=_NO_DATA)

    weather = load_weather_frame(db, station.id, days=10, include_forecast=True)
    if weather.empty:
        raise HTTPException(
            status_code=404,
            detail="No real weather available to run a simulation against.",
        )
    now_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    horizon = weather[weather["observed_at"] >= now_hour]
    if len(horizon) < 6:
        horizon = weather.tail(max(req.duration_h, 24))

    overrides = ScenarioOverrides(
        wind_speed_ms=req.wind_speed_ms,
        solar_radiation_wm2=req.solar_radiation_wm2,
        temperature_c=req.temperature_c,
        demand_multiplier=req.demand_multiplier,
        initial_soc_pct=req.initial_soc_pct,
        initial_fuel_l=req.initial_fuel_l,
        generators_available=req.generators_available,
        turbines_available=req.turbines_available,
    )

    simulator = CrisisSimulator()
    result = simulator.simulate(
        weather=horizon,
        scenario=ScenarioType(req.scenario),
        initial_soc_pct=row.battery_soc_pct,
        initial_fuel_l=row.fuel_level_l,
        duration_h=req.duration_h,
        severity=req.severity,
        overrides=overrides,
    )

    run_at = datetime.now(timezone.utc)
    db.add(CrisisSimulation(
        station_id=station.id, run_at=run_at, scenario=ScenarioType(req.scenario),
        scenario_label=result.label, duration_h=result.duration_h,
        severity=result.severity, parameters=result.parameters,
        survival_hours=result.survival_hours,
        baseline_survival_hours=result.baseline_survival_hours,
        survival_delta_hours=result.survival_delta_hours,
        min_soc_pct=result.min_soc_pct, fuel_used_l=result.fuel_used_l,
        fuel_remaining_l=result.fuel_remaining_l,
        load_shed_kwh=result.load_shed_kwh,
        unserved_critical_kwh=result.unserved_critical_kwh,
        critical_load_secured=result.critical_load_secured,
        blackout_occurred=result.blackout_occurred,
        time_to_first_shed_h=result.time_to_first_shed_h,
        severity_rating=result.severity_rating, timeline=result.timeline,
        actions_taken=result.actions_taken, summary=result.summary,
    ))
    db.commit()

    return Envelope(
        data=CrisisOut(**result.as_dict()),
        provenance=PROVENANCE_SIMULATED,
        generated_at=run_at,
        notice=SIMULATION_DISCLAIMER,
    )


@router.get("/crisis/history")
def crisis_history(limit: int = Query(20, ge=1, le=100),
                   db: Session = Depends(get_db)):
    station = get_station_or_404(db)
    rows = list(db.scalars(
        select(CrisisSimulation)
        .where(CrisisSimulation.station_id == station.id)
        .order_by(CrisisSimulation.run_at.desc()).limit(limit)
    ))
    return {
        "data": [
            {
                "id": r.id, "run_at": r.run_at, "scenario": r.scenario.value,
                "scenario_label": r.scenario_label, "duration_h": r.duration_h,
                "severity": r.severity, "survival_hours": r.survival_hours,
                "baseline_survival_hours": r.baseline_survival_hours,
                "min_soc_pct": r.min_soc_pct, "fuel_used_l": r.fuel_used_l,
                "critical_load_secured": r.critical_load_secured,
                "severity_rating": r.severity_rating, "summary": r.summary,
            }
            for r in rows
        ],
        "disclaimer": SIMULATION_DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# Recommendations & alerts
# ---------------------------------------------------------------------------


@router.get("/recommendations", response_model=Envelope[list[RecommendationOut]])
def recommendations(active_only: bool = Query(True),
                    limit: int = Query(20, ge=1, le=100),
                    db: Session = Depends(get_db)):
    station = get_station_or_404(db)
    stmt = select(Recommendation).where(Recommendation.station_id == station.id)
    if active_only:
        stmt = stmt.where(Recommendation.is_active.is_(True))
    rows = list(db.scalars(
        stmt.order_by(Recommendation.generated_at.desc(), Recommendation.id).limit(limit)
    ))
    return Envelope(
        data=[RecommendationOut.model_validate(r) for r in rows],
        provenance=PROVENANCE_FORECAST,
        generated_at=datetime.now(timezone.utc),
        notice=None if rows else _NO_DATA,
    )


@router.get("/alerts", response_model=Envelope[list[AlertOut]])
def alerts(status: str | None = Query(None), limit: int = Query(50, ge=1, le=200),
           db: Session = Depends(get_db)):
    station = get_station_or_404(db)
    stmt = select(Alert).where(Alert.station_id == station.id)
    if status:
        try:
            stmt = stmt.where(Alert.status == AlertStatus(status.upper()))
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown status '{status}'. Use ACTIVE, ACKNOWLEDGED or RESOLVED.",
            )
    rows = list(db.scalars(stmt.order_by(Alert.raised_at.desc()).limit(limit)))
    return Envelope(
        data=[AlertOut.model_validate(r) for r in rows],
        provenance=PROVENANCE_MODELLED,
        generated_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Explainable AI
# ---------------------------------------------------------------------------


@router.get("/explainability", response_model=Envelope[ExplainabilityOut])
def explainability(db: Session = Depends(get_db)):
    """Why the AI predicted what it predicted, and why the optimiser chose
    the dispatch it chose."""
    station = get_station_or_404(db)

    if not registry.is_trained:
        raise HTTPException(
            status_code=503,
            detail=(
                "Models are not trained yet. They train automatically on the "
                "first refresh once enough real weather history exists."
            ),
        )

    weather = load_weather_frame(db, station.id, days=30, include_forecast=True)
    if weather.empty:
        raise HTTPException(status_code=404, detail=_NO_DATA)

    # After a restart the estimator is restored from disk but the explainer's
    # background sample is not - rebuild it from the stored real weather.
    explainer = registry.ensure_explainer(weather)
    if explainer is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Explainer unavailable: not enough real weather history to "
                "build an attribution background sample."
            ),
        )

    feats = build_features(weather)
    X = select_matrix(feats, LOAD_FEATURES)
    last_idx = X.index[-1]
    explanation = explainer.explain_row(
        X.loc[last_idx],
        timestamp=feats.loc[last_idx, "observed_at"].to_pydatetime(),
    )

    row = latest_energy_row(db, station.id)
    dispatch = None
    if row:
        units, loading = _genset_units_and_loading(row.generator_output_kw)
        dispatch = explain_dispatch({
            "timestamp": row.recorded_at.isoformat(),
            "total_load_kw": row.total_load_kw,
            "renewable_kw": row.wind_generation_kw + row.solar_generation_kw,
            "generator_output_kw": row.generator_output_kw,
            "battery_charge_kw": row.battery_charge_kw,
            "battery_discharge_kw": row.battery_discharge_kw,
            "battery_soc_pct": row.battery_soc_pct,
            "renewable_curtailed_kw": row.renewable_curtailed_kw,
            "shed_load_kw": row.shed_load_kw,
            "generators_running": units,
            "generator_loading_pct": loading,
            "wet_stacking": bool(units and loading < 30.0),
        })

    payload = explanation.as_dict()
    return Envelope(
        data=ExplainabilityOut(
            **payload,
            global_importance=explainer.global_importance(),
            dispatch_explanation=dispatch,
            model_reports=registry.reports,
        ),
        provenance=PROVENANCE_FORECAST,
        generated_at=datetime.now(timezone.utc),
    )
