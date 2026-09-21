"""The POLARIS intelligence pipeline.

Runs on every successful live-weather refresh:

    LIVE WEATHER
      -> preprocessing & feature engineering
      -> AI load forecasting + renewable prediction
      -> energy state estimation
      -> optimization
      -> survival analysis
      -> alerts + recommendations
      -> persisted to PostgreSQL

Nothing here invents a weather value. If the weather history is too thin to
train on, the pipeline says so and degrades to the deterministic physics
model rather than producing a fabricated forecast.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.station import FUEL, SITE
from app.ml.energy_state_estimator import EnergyStateEstimator
from app.ml.registry import registry
from app.models import (
    Alert,
    AlertStatus,
    BatteryState,
    DataProvenance,
    EnergyForecast,
    EnergyParameter,
    FuelState,
    GeneratorState,
    LoadProfile,
    OptimizationResult,
    Recommendation,
    RenewableForecast,
    Station,
    WeatherObservation,
)
from app.models.base import BatteryMode, GeneratorStatus
from app.optimization.energy_optimizer import EnergyOptimizer
from app.services import recommendations as rec_engine
from app.services.energy_model import compute_load, estimate_autonomy, initial_energy_state

log = logging.getLogger("polaris.pipeline")


@dataclass
class PipelineResult:
    ran_at: datetime
    ok: bool
    stages: dict = field(default_factory=dict)
    error: str | None = None
    weather_rows: int = 0
    forecast_rows: int = 0
    recommendations: int = 0
    alerts: int = 0
    duration_ms: float = 0.0

    def as_dict(self) -> dict:
        return {
            "ran_at": self.ran_at.isoformat(),
            "ok": self.ok,
            "stages": self.stages,
            "error": self.error,
            "weather_rows": self.weather_rows,
            "forecast_rows": self.forecast_rows,
            "recommendations": self.recommendations,
            "alerts": self.alerts,
            "duration_ms": round(self.duration_ms, 1),
        }


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------


def load_weather_frame(db: Session, station_id: int, days: int = 120,
                       include_forecast: bool = False) -> pd.DataFrame:
    """Real observations (and optionally the real NWP forecast) as a frame."""
    now = datetime.now(timezone.utc)
    provenances = [DataProvenance.LIVE_OBSERVED, DataProvenance.REAL_OBSERVED]
    if include_forecast:
        provenances.append(DataProvenance.REAL_FORECAST)

    stmt = (
        select(WeatherObservation)
        .where(
            WeatherObservation.station_id == station_id,
            WeatherObservation.observed_at >= now - timedelta(days=days),
            WeatherObservation.provenance.in_(provenances),
        )
        .order_by(WeatherObservation.observed_at)
    )
    rows = list(db.scalars(stmt))
    if not rows:
        return pd.DataFrame()

    recs = [{
        "observed_at": r.observed_at,
        "temperature_c": r.temperature_c,
        "wind_speed_ms": r.wind_speed_ms,
        "wind_direction_deg": r.wind_direction_deg,
        "solar_radiation_wm2": r.solar_radiation_wm2,
        "direct_radiation_wm2": r.direct_radiation_wm2,
        "diffuse_radiation_wm2": r.diffuse_radiation_wm2,
        "humidity_pct": r.humidity_pct,
        "pressure_hpa": r.pressure_hpa,
        "cloud_cover_pct": r.cloud_cover_pct,
        "snowfall_mm": r.snowfall_mm,
        "wind_chill_c": r.wind_chill_c,
        "air_density_kg_m3": r.air_density_kg_m3,
        "is_polar_night": r.is_polar_night,
        "provenance": r.provenance.value,
        "source": r.source,
        "source_provider": r.source_provider,
    } for r in rows]

    df = pd.DataFrame(recs)
    df["observed_at"] = pd.to_datetime(df["observed_at"], utc=True)
    # One row per hour: keep the most authoritative already-resolved record.
    df = (df.sort_values("observed_at")
            .drop_duplicates(subset="observed_at", keep="last")
            .reset_index(drop=True))
    return df


def get_station(db: Session) -> Station:
    st = db.scalar(select(Station).where(Station.code == SITE.station_code))
    if st is None:
        raise RuntimeError(f"Station {SITE.station_code} not provisioned")
    return st


#: Real hourly observations needed before the ML models can be trained.
MIN_HISTORY_ROWS = 1200


async def backfill_history_if_needed(db: Session, days: int = 90) -> int:
    """Top up real historical weather when the store is too thin to train on.

    Called from the scheduled refresh so a deployment is self-healing: if the
    one-off boot backfill was rate-limited (common on shared-IP hosting,
    where another tenant can exhaust a per-IP quota), the next cycle simply
    tries again. Once enough history exists this returns immediately.

    It never fabricates history - a failure just leaves the store thin and
    the models untrained, which the API reports honestly.
    """
    from app.services.weather.ingest import (
        get_primary_station,
        persist_readings,
        update_source_status,
    )
    from app.services.weather.providers import OpenMeteoArchiveProvider

    station = get_primary_station(db)
    existing = db.scalar(
        select(func.count(WeatherObservation.id)).where(
            WeatherObservation.station_id == station.id,
            WeatherObservation.provenance == DataProvenance.REAL_OBSERVED,
        )
    ) or 0
    if existing >= MIN_HISTORY_ROWS:
        return 0

    end = datetime.now(timezone.utc).date() - timedelta(days=6)  # ERA5 lag
    start = end - timedelta(days=days)
    provider = OpenMeteoArchiveProvider(
        start_date=start.isoformat(), end_date=end.isoformat()
    )
    log.info("History is thin (%d rows); attempting ERA5 backfill %s -> %s",
             existing, start, end)
    outcome = await provider.fetch()
    if not outcome.ok:
        log.warning("Backfill attempt failed (%s). Will retry next cycle.",
                    outcome.error)
        update_source_status(db, provider, outcome, is_active=False)
        db.commit()
        return 0

    written, _ = persist_readings(
        db, station.id, outcome.usable_readings,
        fetched_at=datetime.now(timezone.utc),
    )
    update_source_status(db, provider, outcome, is_active=False)
    db.commit()
    log.info("Backfilled %d real historical observations", written)
    return written


def anchor_energy_state() -> dict:
    """The fixed starting point for the modelled window.

    The pipeline re-simulates the whole retained window on every run, so it
    must always start from the SAME anchor. Carrying the previous run's end
    state forward would re-burn the same hours of fuel on every refresh -
    the tank would drain to empty after enough refreshes even though no extra
    real time had passed.

    Anchoring makes the model deterministic: the same weather history always
    yields the same modelled state, which is what a reproducible model owes
    its operator.
    """
    return initial_energy_state()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_pipeline(db: Session, trigger: str = "scheduled",
                 horizon_h: int | None = None,
                 retrain: bool = False) -> PipelineResult:
    started = datetime.now(timezone.utc)
    result = PipelineResult(ran_at=started, ok=False)
    horizon_h = horizon_h or settings.forecast_horizon_h
    history_days = settings.history_days

    try:
        station = get_station(db)

        # -- 1. preprocessing ------------------------------------------------
        history = load_weather_frame(db, station.id, days=history_days)
        result.weather_rows = len(history)
        if history.empty:
            result.error = (
                "No real weather observations in the database. Run a refresh "
                "first: POST /api/weather/refresh"
            )
            result.stages["preprocessing"] = {"ok": False, "rows": 0}
            return result
        result.stages["preprocessing"] = {
            "ok": True, "rows": len(history),
            "span": [history["observed_at"].min().isoformat(),
                     history["observed_at"].max().isoformat()],
            "sources": sorted(history["source_provider"].dropna().unique().tolist()),
        }

        # -- 2. train / restore models --------------------------------------
        if retrain or not registry.is_trained:
            registry.train(history, force=retrain)
        result.stages["training"] = registry.status()

        # -- 3. forecast horizon (REAL NWP forecast rows) --------------------
        full = load_weather_frame(db, station.id, days=history_days,
                                  include_forecast=True)
        now_hour = started.replace(minute=0, second=0, microsecond=0)
        future = full[full["observed_at"] >= now_hour].head(horizon_h).copy()
        if future.empty:
            future = full.tail(min(horizon_h, len(full))).copy()

        if registry.is_trained and not future.empty:
            load_fc = registry.load_forecaster.predict(future)
            renew_fc = registry.renewable_forecaster.predict(future)
            forecast = load_fc.merge(renew_fc, on="target_time", how="inner",
                                     suffixes=("", "_r"))
        else:
            # Physics-only degradation - clearly flagged, never fabricated.
            from app.ml.renewable_forecaster import RenewableForecaster
            from app.ml.features import build_features

            feats = build_features(future)
            base = RenewableForecaster.physical_baseline(feats)
            rows = [compute_load(t.to_pydatetime(), float(tc), float(wc), bool(pn))
                    for t, tc, wc, pn in zip(
                        feats["observed_at"], feats["temperature_c"],
                        feats["wind_chill_c"], feats["is_polar_night"])]
            forecast = pd.DataFrame({
                "target_time": feats["observed_at"],
                "predicted_load_kw": [r.total_kw for r in rows],
                "critical_load_kw": [r.critical_kw for r in rows],
                "deferrable_load_kw": [r.deferrable_kw for r in rows],
                "wind_kw": base["wind_physical_kw"].to_numpy(),
                "solar_kw": base["solar_physical_kw"].to_numpy(),
                "total_kw": (base["wind_physical_kw"] + base["solar_physical_kw"]).to_numpy(),
            })

        forecast = forecast.merge(
            future[["observed_at", "temperature_c", "wind_speed_ms",
                    "solar_radiation_wm2", "humidity_pct", "cloud_cover_pct",
                    "air_density_kg_m3", "wind_chill_c", "is_polar_night",
                    "provenance", "source"]],
            left_on="target_time", right_on="observed_at", how="left",
        )
        result.forecast_rows = len(forecast)
        result.stages["forecasting"] = {
            "ok": True, "rows": len(forecast),
            "mode": "ML" if registry.is_trained else "PHYSICS_ONLY",
            "horizon_h": horizon_h,
        }

        # -- 4. energy state estimation --------------------------------------
        # Always re-simulate the retained window from the fixed anchor; see
        # anchor_energy_state() for why carrying forward would be wrong.
        state0 = anchor_energy_state()
        estimator = EnergyStateEstimator()

        recent = full[full["observed_at"] < now_hour].tail(72).copy()
        if recent.empty:
            recent = history.tail(24).copy()
        sim = estimator.run(recent, initial_soc_pct=state0["soc_pct"],
                            initial_fuel_l=state0["fuel_l"])
        current = sim.states[-1] if sim.states else None
        result.stages["state_estimation"] = {
            "ok": current is not None,
            "hours_simulated": len(sim.states),
            "final_soc_pct": sim.final_soc_pct,
            "final_fuel_l": sim.final_fuel_l,
        }
        if current is None:
            result.error = "State estimation produced no states"
            return result

        _persist_energy_state(db, station.id, sim, recent)

        # -- 5. optimization ---------------------------------------------------
        optimizer = EnergyOptimizer()
        opt = optimizer.optimize(
            forecast, initial_soc_pct=current.battery_soc_pct,
            initial_fuel_l=current.fuel_level_l, horizon_h=min(48, len(forecast)),
        )
        opt_dict = {
            "horizon_h": min(48, len(forecast)),
            "baseline_fuel_l": opt.baseline_fuel_l,
            "optimized_fuel_l": opt.optimized_fuel_l,
            "fuel_saved_l": opt.fuel_saved_l,
            "fuel_saved_pct": opt.fuel_saved_pct,
            "renewable_fraction": opt.renewable_fraction,
            "load_deferred_kwh": opt.load_deferred_kwh,
            "co2_avoided_kg": opt.co2_avoided_kg,
            "feasible": opt.feasible,
        }
        db.add(OptimizationResult(
            station_id=station.id, run_at=started, horizon_h=min(48, len(forecast)),
            baseline_fuel_l=opt.baseline_fuel_l, optimized_fuel_l=opt.optimized_fuel_l,
            fuel_saved_l=opt.fuel_saved_l, fuel_saved_pct=opt.fuel_saved_pct,
            renewable_fraction=opt.renewable_fraction,
            renewable_curtailed_kwh=opt.renewable_curtailed_kwh,
            generator_runtime_h=opt.generator_runtime_h,
            generator_starts=opt.generator_starts,
            load_shed_kwh=opt.load_shed_kwh, load_deferred_kwh=opt.load_deferred_kwh,
            unserved_critical_kwh=opt.unserved_critical_kwh,
            battery_throughput_kwh=opt.battery_throughput_kwh,
            final_soc_pct=opt.final_soc_pct, co2_avoided_kg=opt.co2_avoided_kg,
            feasible=opt.feasible, solve_ms=opt.solve_ms,
            schedule=[h.as_dict() for h in opt.schedule],
            rationale=opt.rationale, constraints_binding=opt.constraints_binding,
        ))
        result.stages["optimization"] = opt_dict

        # -- 6. survival analysis ---------------------------------------------
        autonomy = estimate_autonomy(
            critical_demand_kw=current.critical_load_kw,
            soc_pct=current.battery_soc_pct,
            fuel_l=current.fuel_level_l,
            ambient_c=current.temperature_c,
            renewable_kw=current.renewable_kw,
        )
        autonomy_dict = {
            "hours": None if autonomy.hours == float("inf") else autonomy.hours,
            "battery_hours": None if autonomy.battery_hours == float("inf") else autonomy.battery_hours,
            "fuel_hours": None if autonomy.fuel_hours == float("inf") else autonomy.fuel_hours,
            "limited_by": autonomy.limited_by,
            "battery_available_kwh": autonomy.battery_available_kwh,
            "fuel_usable_l": autonomy.fuel_usable_l,
            "critical_demand_kw": autonomy.critical_demand_kw,
            "renewable_contribution_kw": autonomy.renewable_contribution_kw,
        }
        result.stages["survival"] = autonomy_dict

        # -- 7. persist AI forecasts -----------------------------------------
        _persist_forecasts(db, station.id, started, forecast)

        # -- 8. recommendations + alerts --------------------------------------
        weather_now = {
            "temperature_c": current.temperature_c,
            "wind_speed_ms": current.wind_speed_ms,
        }
        # Forward-looking context so the recommendation engine can reason
        # about trends ("renewables falling", "demand rising") rather than
        # only the present instant.
        fc_head = forecast.head(24)
        fc_summary = {
            "rows": len(forecast),
            "horizon_h": int(len(fc_head)),
            "mean_load_kw": round(float(fc_head["predicted_load_kw"].mean()), 3),
            "max_load_kw": round(float(fc_head["predicted_load_kw"].max()), 3),
            "mean_renewable_kw": round(
                float((fc_head["wind_kw"] + fc_head["solar_kw"]).mean()), 3
            ),
            "min_renewable_kw": round(
                float((fc_head["wind_kw"] + fc_head["solar_kw"]).min()), 3
            ),
        } if not fc_head.empty else None

        recs, alerts = rec_engine.generate(
            state=current.as_dict(), autonomy=autonomy_dict,
            optimization=opt_dict,
            forecast_summary=fc_summary,
            weather=weather_now,
        )
        _persist_recommendations(db, station.id, started, recs)
        _persist_alerts(db, station.id, started, alerts)
        result.recommendations = len(recs)
        result.alerts = len(alerts)
        result.stages["recommendations"] = {
            "ok": True, "count": len(recs), "alerts": len(alerts)
        }

        db.commit()
        result.ok = True
    except Exception as exc:
        db.rollback()
        result.error = f"{type(exc).__name__}: {exc}"
        log.exception("Pipeline failed")

    result.duration_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    return result


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _persist_energy_state(db: Session, station_id: int, sim, weather: pd.DataFrame) -> None:
    """Replace the modelled state window with the freshly computed one."""
    if not sim.states:
        return
    first_ts = sim.states[0].timestamp
    for model in (EnergyParameter, BatteryState, GeneratorState, FuelState, LoadProfile):
        ts_col = getattr(model, "recorded_at")
        db.execute(delete(model).where(
            model.station_id == station_id, ts_col >= first_ts
        ))

    for s in sim.states:
        db.add(EnergyParameter(
            station_id=station_id, recorded_at=s.timestamp,
            total_load_kw=s.total_load_kw, critical_load_kw=s.critical_load_kw,
            deferrable_load_kw=s.deferrable_load_kw, shed_load_kw=s.shed_load_kw,
            unserved_load_kw=s.unserved_load_kw,
            wind_generation_kw=s.wind_generation_kw,
            solar_generation_kw=s.solar_generation_kw,
            generator_output_kw=s.generator_output_kw,
            renewable_curtailed_kw=s.renewable_curtailed_kw,
            battery_charge_kw=s.battery_charge_kw,
            battery_discharge_kw=s.battery_discharge_kw,
            battery_soc_pct=s.battery_soc_pct,
            fuel_consumed_l=s.fuel_consumed_l, fuel_level_l=s.fuel_level_l,
            renewable_fraction=s.renewable_fraction, co2_kg=s.co2_kg,
            temperature_c=s.temperature_c, wind_speed_ms=s.wind_speed_ms,
            provenance=DataProvenance.MODELLED,
        ))
        db.add(BatteryState(
            station_id=station_id, recorded_at=s.timestamp,
            soc_pct=s.battery_soc_pct, stored_kwh=s.battery_stored_kwh,
            usable_kwh=s.battery_stored_kwh,
            nominal_capacity_kwh=600.0, effective_capacity_kwh=600.0,
            power_kw=s.battery_charge_kw - s.battery_discharge_kw,
            mode=BatteryMode(s.battery_mode) if s.battery_mode in
                 BatteryMode.__members__ else BatteryMode.IDLE,
            temperature_c=s.temperature_c,
        ))
        db.add(GeneratorState(
            station_id=station_id, recorded_at=s.timestamp, unit_id=1,
            unit_label="DG-1", rated_kw=100.0,
            status=(GeneratorStatus.RUNNING if s.generators_running > 0
                    else GeneratorStatus.OFFLINE),
            output_kw=s.generator_output_kw, loading_pct=s.generator_loading_pct,
            fuel_rate_lph=s.fuel_consumed_l,
            efficiency_pct=s.generator_efficiency * 100.0,
            wet_stacking_risk=s.wet_stacking,
        ))
        db.add(FuelState(
            station_id=station_id, recorded_at=s.timestamp,
            level_l=s.fuel_level_l, capacity_l=FUEL.tank_capacity_l,
            level_pct=round(s.fuel_level_l / FUEL.tank_capacity_l * 100, 2),
            consumption_rate_lph=s.fuel_consumed_l,
            energy_content_kwh=round(s.fuel_level_l * FUEL.energy_kwh_per_l, 1),
            below_critical_reserve=s.fuel_level_l <= FUEL.critical_level_l,
        ))

    # Per-channel load breakdown for the most recent hour only (keeps the
    # table useful without exploding row count).
    last = sim.states[-1]
    load = compute_load(last.timestamp, last.temperature_c)
    for ch in load.channels:
        db.add(LoadProfile(
            station_id=station_id, recorded_at=last.timestamp,
            channel_key=ch.key, channel_label=ch.label,
            priority=ch.priority.value, demand_kw=ch.demand_kw,
            served_kw=ch.demand_kw, is_deferrable=ch.is_deferrable,
        ))
    db.flush()


def _persist_forecasts(db: Session, station_id: int, run_at: datetime,
                       forecast: pd.DataFrame) -> None:
    db.execute(delete(EnergyForecast).where(EnergyForecast.station_id == station_id))
    db.execute(delete(RenewableForecast).where(RenewableForecast.station_id == station_id))

    for i, r in enumerate(forecast.to_dict("records")):
        tt = r["target_time"]
        if hasattr(tt, "to_pydatetime"):
            tt = tt.to_pydatetime()
        db.add(EnergyForecast(
            station_id=station_id, run_at=run_at, target_time=tt, horizon_h=i,
            predicted_load_kw=float(r.get("predicted_load_kw", 0.0)),
            load_kw_p10=r.get("load_kw_p10"), load_kw_p90=r.get("load_kw_p90"),
            critical_load_kw=float(r.get("critical_load_kw", 0.0)),
            deferrable_load_kw=float(r.get("deferrable_load_kw", 0.0)),
            predicted_energy_kwh=float(r.get("predicted_load_kw", 0.0)),
            input_temperature_c=r.get("temperature_c"),
            input_wind_speed_ms=r.get("wind_speed_ms"),
            weather_provenance=r.get("provenance"),
            weather_source=r.get("source"),
            model_version=r.get("model_version"),
            model_name=r.get("model_name"),
            confidence=r.get("confidence"),
        ))
        db.add(RenewableForecast(
            station_id=station_id, run_at=run_at, target_time=tt, horizon_h=i,
            wind_kw=float(r.get("wind_kw", 0.0)),
            solar_kw=float(r.get("solar_kw", 0.0)),
            total_kw=float(r.get("wind_kw", 0.0)) + float(r.get("solar_kw", 0.0)),
            wind_kw_p10=r.get("wind_kw_p10"), wind_kw_p90=r.get("wind_kw_p90"),
            solar_kw_p10=r.get("solar_kw_p10"), solar_kw_p90=r.get("solar_kw_p90"),
            wind_physical_kw=r.get("wind_physical_kw"),
            solar_physical_kw=r.get("solar_physical_kw"),
            ml_correction_kw=r.get("ml_correction_kw"),
            input_temperature_c=r.get("temperature_c"),
            input_wind_speed_ms=r.get("wind_speed_ms"),
            input_solar_radiation_wm2=r.get("solar_radiation_wm2"),
            weather_provenance=r.get("provenance"),
            weather_source=r.get("source"),
            turbine_curtailed=bool(r.get("turbine_curtailed", False)),
            icing_risk=bool(r.get("icing_risk", False)),
            model_version=r.get("model_version"),
        ))
    db.flush()


def _persist_recommendations(db: Session, station_id: int, run_at: datetime,
                             recs: list) -> None:
    db.execute(
        Recommendation.__table__.update()
        .where(Recommendation.station_id == station_id)
        .values(is_active=False)
    )
    for r in recs:
        db.add(Recommendation(
            station_id=station_id, generated_at=run_at, category=r.category,
            urgency=r.urgency, title=r.title, action=r.action,
            rationale=r.rationale, drivers=r.drivers, reasons=r.reasons,
            expected_benefit=r.expected_benefit,
            estimated_fuel_saving_l=r.estimated_fuel_saving_l,
            estimated_autonomy_gain_h=r.estimated_autonomy_gain_h,
            confidence=r.confidence, evidence=r.evidence,
            counterfactual=r.counterfactual, is_active=True,
            source_module=r.source_module,
        ))
    db.flush()


def _persist_alerts(db: Session, station_id: int, run_at: datetime,
                    alerts: list) -> None:
    existing = {
        a.code: a for a in db.scalars(
            select(Alert).where(
                Alert.station_id == station_id,
                Alert.status == AlertStatus.ACTIVE,
            )
        )
    }
    incoming = {a.code for a in alerts}

    for spec in alerts:
        if spec.code in existing:  # refresh the live value on an open alert
            row = existing[spec.code]
            row.metric_value = spec.metric_value
            row.message = spec.message
            continue
        db.add(Alert(
            station_id=station_id, raised_at=run_at, code=spec.code,
            severity=spec.severity, status=AlertStatus.ACTIVE, title=spec.title,
            message=spec.message, subsystem=spec.subsystem,
            metric_name=spec.metric_name, metric_value=spec.metric_value,
            threshold_value=spec.threshold_value,
            recommended_action=spec.recommended_action, detail=spec.detail,
        ))

    # Auto-resolve alerts whose condition has cleared.
    for code, row in existing.items():
        if code not in incoming:
            row.status = AlertStatus.RESOLVED
            row.resolved_at = run_at
    db.flush()
