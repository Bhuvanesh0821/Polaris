"""Energy model, ML forecasting, optimisation, survival and risk."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from app.core.station import BATTERY, FUEL
from app.ml.anomaly_detector import AnomalyDetector
from app.ml.energy_state_estimator import EnergyStateEstimator
from app.ml.features import LOAD_FEATURES, build_features, select_matrix
from app.ml.load_forecaster import LoadForecaster
from app.ml.renewable_forecaster import RenewableForecaster
from app.optimization.energy_optimizer import EnergyOptimizer
from app.services import risk as risk_engine
from app.services.energy_model import (
    autonomy_modes,
    compute_generation,
    compute_load,
    estimate_autonomy,
)


# ----------------------------------------------------------- load model ---

def test_colder_weather_raises_heating_load():
    ts = datetime(2026, 7, 15, 8, tzinfo=timezone.utc)
    mild = compute_load(ts, -5.0, wind_chill_c=-8.0)
    harsh = compute_load(ts, -45.0, wind_chill_c=-55.0)
    assert harsh.total_kw > mild.total_kw
    assert harsh.heating_kw > mild.heating_kw


def test_critical_load_is_a_subset_of_total():
    load = compute_load(datetime(2026, 7, 15, 8, tzinfo=timezone.utc), -25.0)
    assert 0 < load.critical_kw < load.total_kw
    assert load.life_critical_kw <= load.critical_kw


def test_nan_inputs_do_not_silently_zero_the_load():
    """A NaN wind chill must be recomputed, not treated as 'no heating'."""
    ts = datetime(2026, 7, 15, 8, tzinfo=timezone.utc)
    ref = compute_load(ts, -30.0, wind_chill_c=-38.0)
    nan_in = compute_load(ts, -30.0, wind_chill_c=float("nan"), wind_speed_ms=12.0)
    assert nan_in.heating_kw > 0
    assert nan_in.total_kw == pytest.approx(ref.total_kw, rel=0.15)


def test_missing_temperature_is_rejected_not_guessed():
    with pytest.raises(ValueError):
        compute_load(datetime(2026, 7, 1, tzinfo=timezone.utc), float("nan"))


def test_summer_crew_exceeds_winter_crew():
    summer = compute_load(datetime(2026, 1, 15, 12, tzinfo=timezone.utc), -3.0)
    winter = compute_load(datetime(2026, 7, 15, 12, tzinfo=timezone.utc), -3.0)
    assert summer.crew > winter.crew


def test_generation_recomputes_missing_air_density():
    gen = compute_generation(
        ts=datetime(2026, 7, 15, 12, tzinfo=timezone.utc),
        temperature_c=-30.0, wind_speed_ms=10.0,
        air_density_kg_m3=float("nan"),
    )
    assert gen.wind_kw > 0


# -------------------------------------------------------------- ML -------

def test_load_forecaster_trains_and_predicts(weather_frame):
    lf = LoadForecaster()
    rep = lf.fit(weather_frame)
    assert rep.n_samples > 100
    assert rep.r2 > 0.8
    out = lf.predict(weather_frame.tail(48))
    assert len(out) == 48
    assert (out["predicted_load_kw"] > 0).all()
    # prediction interval must bracket the point estimate
    assert (out["load_kw_p10"] <= out["predicted_load_kw"]).all()
    assert (out["load_kw_p90"] >= out["predicted_load_kw"]).all()


def test_load_forecaster_rejects_tiny_datasets():
    import pandas as pd
    with pytest.raises(ValueError):
        LoadForecaster().fit(pd.DataFrame({
            "observed_at": pd.date_range("2026-01-01", periods=5, freq="h", tz="UTC"),
            "temperature_c": [-20] * 5, "wind_speed_ms": [5] * 5,
        }))


def test_renewable_forecaster_respects_nameplate(weather_frame):
    rf = RenewableForecaster()
    rf.fit(weather_frame)
    out = rf.predict(weather_frame.tail(48))
    from app.core.station import SOLAR, WIND
    assert (out["wind_kw"] <= WIND.rated_kw + 1e-6).all()
    assert (out["solar_kw"] <= SOLAR.rated_kwp + 1e-6).all()
    assert (out["wind_kw"] >= 0).all()


def test_no_solar_during_polar_night(weather_frame):
    rf = RenewableForecaster()
    rf.fit(weather_frame)
    out = rf.predict(weather_frame.tail(72))
    assert out["solar_kw"].max() < 0.5


def test_solar_is_produced_in_summer(summer_frame):
    rf = RenewableForecaster()
    rf.fit(summer_frame)
    out = rf.predict(summer_frame.tail(48))
    assert out["solar_kw"].max() > 1.0


def test_features_have_no_nans(weather_frame):
    X = select_matrix(build_features(weather_frame), LOAD_FEATURES)
    assert X.isna().sum().sum() == 0
    assert X.shape[1] == len(LOAD_FEATURES)


def test_features_do_not_leak_the_future(weather_frame):
    """Lag columns must equal the shifted series, never the current value."""
    f = build_features(weather_frame)
    lag = f["temp_lag_1"].to_numpy()[1:]
    cur = f["temperature_c"].to_numpy()[:-1]
    assert np.allclose(lag, cur, equal_nan=True)


# ------------------------------------------------- state estimation ------

def test_state_estimator_conserves_fuel(weather_frame):
    sim = EnergyStateEstimator().run(
        weather_frame.tail(48), initial_soc_pct=70, initial_fuel_l=50_000
    )
    burned = 50_000 - sim.final_fuel_l
    assert burned >= 0
    assert burned == pytest.approx(sim.total_fuel_l, rel=0.02)


def test_battery_stays_within_its_band(weather_frame):
    sim = EnergyStateEstimator().run(
        weather_frame.tail(72), initial_soc_pct=60, initial_fuel_l=50_000
    )
    for s in sim.states:
        assert BATTERY.soc_min_pct - 1 <= s.battery_soc_pct <= BATTERY.soc_max_pct + 1


def test_battery_recharges_rather_than_pinning_at_floor(weather_frame):
    """Once at the floor the genset must recharge, not leave it flat."""
    sim = EnergyStateEstimator().run(
        weather_frame.tail(72), initial_soc_pct=16, initial_fuel_l=50_000
    )
    socs = [s.battery_soc_pct for s in sim.states]
    assert max(socs) > min(socs) + 3.0


def test_estimator_is_deterministic(weather_frame):
    a = EnergyStateEstimator().run(weather_frame.tail(48), 60, 50_000)
    b = EnergyStateEstimator().run(weather_frame.tail(48), 60, 50_000)
    assert a.final_fuel_l == b.final_fuel_l
    assert a.final_soc_pct == b.final_soc_pct


def test_anomaly_detector_flags_low_battery(weather_frame):
    sim = EnergyStateEstimator().run(weather_frame.tail(96), 25, 20_000)
    states = sim.to_frame()
    det = AnomalyDetector()
    det.fit(states)
    found = det.detect(states)
    assert any("BATTERY" in a.code for a in found)


# ----------------------------------------------------------- survival ----

def test_autonomy_falls_as_reserves_fall():
    high = estimate_autonomy(60, 90, 50_000, -25, 0)
    low = estimate_autonomy(60, 20, 14_000, -25, 0)
    assert high.hours > low.hours


def test_renewables_extend_autonomy():
    without = estimate_autonomy(60, 50, 40_000, -25, renewable_kw=0)
    with_ren = estimate_autonomy(60, 50, 40_000, -25, renewable_kw=30)
    assert with_ren.hours > without.hours


def test_autonomy_unbounded_when_renewables_cover_demand():
    est = estimate_autonomy(20, 50, 40_000, -25, renewable_kw=60)
    assert est.hours == float("inf")


def test_fuel_reserve_is_withheld():
    est = estimate_autonomy(60, 50, FUEL.critical_level_l, -25, 0)
    assert est.fuel_usable_l == pytest.approx(0.0, abs=1.0)


def test_three_autonomy_modes_are_ordered():
    """Critical-only must outlast normal; crisis must not beat critical-only."""
    m = autonomy_modes(
        total_demand_kw=140, critical_demand_kw=75, soc_pct=60,
        fuel_l=45_000, ambient_c=-25, renewable_kw=20,
    )
    assert m.critical_only.hours > m.normal.hours
    assert m.crisis.hours <= m.critical_only.hours


# --------------------------------------------------------- optimisation ---

def _forecast(weather_frame, lf, rf, n=48):
    a = lf.predict(weather_frame.tail(n))
    b = rf.predict(weather_frame.tail(n))
    out = a.merge(b, on="target_time")
    out["temperature_c"] = weather_frame.tail(n)["temperature_c"].to_numpy()
    return out


def test_optimiser_never_returns_a_worse_plan(weather_frame):
    lf = LoadForecaster(); lf.fit(weather_frame)
    rf = RenewableForecaster(); rf.fit(weather_frame)
    fc = _forecast(weather_frame, lf, rf)
    res = EnergyOptimizer().optimize(fc, 60, 45_000, horizon_h=48)
    assert res.fuel_saved_l >= -1e-6
    assert res.optimized_fuel_l <= res.baseline_fuel_l + 1e-6


def test_optimiser_conserves_deferrable_energy(weather_frame):
    """Shifting deferrable load must not create or destroy energy."""
    lf = LoadForecaster(); lf.fit(weather_frame)
    rf = RenewableForecaster(); rf.fit(weather_frame)
    fc = _forecast(weather_frame, lf, rf)
    res = EnergyOptimizer().optimize(fc, 60, 45_000, horizon_h=48)
    orig = sum(h.deferrable_original_kw for h in res.schedule)
    sched = sum(h.deferrable_scheduled_kw for h in res.schedule)
    assert sched == pytest.approx(orig, rel=0.02)


def test_optimiser_produces_a_full_schedule(weather_frame):
    lf = LoadForecaster(); lf.fit(weather_frame)
    rf = RenewableForecaster(); rf.fit(weather_frame)
    fc = _forecast(weather_frame, lf, rf)
    res = EnergyOptimizer().optimize(fc, 60, 45_000, horizon_h=48)
    assert len(res.schedule) == 48
    assert res.rationale
    for h in res.schedule:
        assert h.reason


# --------------------------------------------------------------- risk ----

def _risk(**over):
    energy = {
        "battery_soc_pct": 70, "fuel_level_l": 50_000, "total_load_kw": 120,
        "critical_load_kw": 70, "renewable_kw": 60, "shed_load_kw": 0,
        "unserved_load_kw": 0, "fuel_consumed_l": 5, "generators_running": 1,
    }
    energy.update(over)
    return risk_engine.assess(energy, over.pop("autonomy_h", 400.0),
                              {"temperature_c": -20, "wind_speed_ms": 8})


def test_risk_is_low_when_everything_is_healthy():
    r = _risk()
    assert r.level in (risk_engine.RiskLevel.LOW, risk_engine.RiskLevel.MODERATE)


def test_risk_rises_as_battery_falls():
    good = _risk(battery_soc_pct=85)
    bad = _risk(battery_soc_pct=16)
    assert bad.score > good.score


def test_unserved_critical_load_forces_severe():
    r = _risk(unserved_load_kw=12.0)
    assert r.level == risk_engine.RiskLevel.SEVERE


def test_risk_factors_are_explainable():
    r = _risk()
    assert r.factors
    for f in r.factors:
        assert f.label and f.value and f.detail
        assert 0.0 <= f.score <= 1.0
    assert r.headline


def test_risk_score_is_bounded():
    worst = _risk(battery_soc_pct=15, fuel_level_l=0, renewable_kw=0,
                  shed_load_kw=50, autonomy_h=1.0)
    assert 0.0 <= worst.score <= 1.0
