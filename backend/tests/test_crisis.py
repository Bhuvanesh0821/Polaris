"""Crisis simulator - every scenario required by the specification."""

from __future__ import annotations

import pytest

from app.models.base import ScenarioType
from app.simulation.crisis_simulator import (
    SCENARIOS,
    CrisisSimulator,
    ScenarioOverrides,
)

ALL_SCENARIOS = [s for s in ScenarioType]


@pytest.fixture(scope="module")
def sim() -> CrisisSimulator:
    return CrisisSimulator()


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda s: s.value)
def test_every_scenario_runs(sim, weather_frame, scenario):
    res = sim.simulate(weather_frame.tail(72), scenario,
                       initial_soc_pct=60, initial_fuel_l=45_000, duration_h=48)
    assert res.duration_h > 0
    assert res.timeline
    assert res.summary
    assert res.severity_rating in (
        "MANAGEABLE", "ELEVATED", "SEVERE", "CATASTROPHIC")


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda s: s.value)
def test_every_scenario_is_labelled_simulated(sim, weather_frame, scenario):
    """The disclaimer is the project's core honesty guarantee."""
    res = sim.simulate(weather_frame.tail(72), scenario, 60, 45_000, duration_h=24)
    assert "SIMULATED" in res.disclaimer
    assert "NOT LIVE" in res.disclaimer


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda s: s.value)
def test_every_scenario_has_before_after_and_recommendation(sim, weather_frame, scenario):
    res = sim.simulate(weather_frame.tail(72), scenario, 60, 45_000, duration_h=24)
    for snap in (res.before, res.after):
        assert snap
        for key in ("battery_soc_pct", "fuel_level_l", "total_load_kw",
                    "critical_load_kw", "autonomy_hours", "risk_level"):
            assert key in snap
    assert res.recommendation["action"]
    assert res.recommendation["reasons"]
    assert res.recommendation["urgency"] in (
        "ROUTINE", "ELEVATED", "URGENT", "IMMEDIATE")


def test_normal_operation_is_the_mildest_scenario(sim, weather_frame):
    normal = sim.simulate(weather_frame.tail(72), ScenarioType.NORMAL_OPERATION,
                          60, 45_000, duration_h=48)
    storm = sim.simulate(weather_frame.tail(72), ScenarioType.SEVERE_STORM,
                         60, 45_000, duration_h=48)
    assert normal.survival_hours >= storm.survival_hours
    assert normal.fuel_used_l <= storm.fuel_used_l


def test_storm_removes_wind_generation(sim, weather_frame):
    """Above cut-out the turbines feather, which is the scenario's whole point."""
    res = sim.simulate(weather_frame.tail(72), ScenarioType.SEVERE_STORM,
                       60, 45_000, duration_h=48)
    assert max(t["wind_kw"] for t in res.timeline) < 1.0


def test_wind_failure_zeroes_the_array(sim, weather_frame):
    res = sim.simulate(weather_frame.tail(72), ScenarioType.WIND_FAILURE,
                       60, 45_000, duration_h=48)
    assert all(t["wind_kw"] == 0 for t in res.timeline)


def test_low_solar_scenario_has_no_solar(sim, summer_frame):
    res = sim.simulate(summer_frame.tail(72), ScenarioType.LOW_SOLAR,
                       60, 45_000, duration_h=48)
    assert max(t["solar_kw"] for t in res.timeline) < 2.0


def test_high_demand_raises_load(sim, weather_frame):
    base = sim.simulate(weather_frame.tail(72), ScenarioType.NORMAL_OPERATION,
                        60, 45_000, duration_h=24)
    high = sim.simulate(weather_frame.tail(72), ScenarioType.HIGH_DEMAND,
                        60, 45_000, duration_h=24)
    assert (max(t["load_kw"] for t in high.timeline)
            > max(t["load_kw"] for t in base.timeline))


def test_generator_failure_limits_dispatch(sim, weather_frame):
    res = sim.simulate(weather_frame.tail(72), ScenarioType.GENERATOR_FAILURE,
                       60, 45_000, duration_h=48)
    # only one 100 kW unit may run
    assert max(t["generator_kw"] for t in res.timeline) <= 100.0 + 1e-6


def test_combined_crisis_is_the_worst_case(sim, weather_frame):
    normal = sim.simulate(weather_frame.tail(72), ScenarioType.NORMAL_OPERATION,
                          60, 45_000, duration_h=48)
    combined = sim.simulate(weather_frame.tail(72), ScenarioType.COMBINED_CRISIS,
                            60, 45_000, duration_h=48)
    assert combined.survival_hours < normal.survival_hours


def test_low_battery_start_reduces_autonomy(sim, weather_frame):
    full = sim.simulate(weather_frame.tail(72), ScenarioType.NORMAL_OPERATION,
                        90, 45_000, duration_h=48)
    flat = sim.simulate(weather_frame.tail(72), ScenarioType.NORMAL_OPERATION,
                        18, 45_000, duration_h=48)
    assert flat.min_soc_pct <= full.min_soc_pct


# ----------------------------------------------------- operator overrides --

def test_overrides_change_the_outcome(sim, weather_frame):
    """Removing renewables must increase diesel consumption.

    Fuel burn is the unambiguous invariant here, not autonomy. Forcing wind
    to zero also removes the WIND CHILL that drives building heat loss, so
    critical demand drops at the same time - the two effects partly cancel in
    the autonomy figure. Fuel used has no such ambiguity.
    """
    base = sim.simulate(weather_frame.tail(72), ScenarioType.NORMAL_OPERATION,
                        60, 45_000, duration_h=48)
    forced = sim.simulate(
        weather_frame.tail(72), ScenarioType.NORMAL_OPERATION, 60, 45_000,
        duration_h=48,
        overrides=ScenarioOverrides(wind_speed_ms=0.0, solar_radiation_wm2=0.0),
    )
    assert forced.fuel_used_l > base.fuel_used_l
    assert max(t["renewable_kw"] for t in forced.timeline) == 0.0


def test_override_wins_over_the_preset(sim, weather_frame):
    """A slider must beat the scenario it was nudged away from."""
    res = sim.simulate(
        weather_frame.tail(72), ScenarioType.SEVERE_STORM, 60, 45_000,
        duration_h=24, overrides=ScenarioOverrides(wind_speed_ms=9.0),
    )
    # 9 m/s is inside the operating band, so the turbines must produce
    assert max(t["wind_kw"] for t in res.timeline) > 0.0


def test_fuel_override_is_respected(sim, weather_frame):
    res = sim.simulate(
        weather_frame.tail(72), ScenarioType.NORMAL_OPERATION, 60, 45_000,
        duration_h=24, overrides=ScenarioOverrides(initial_fuel_l=8_000),
    )
    assert res.timeline[0]["fuel_level_l"] <= 8_000


def test_zero_generators_is_survivable_input(sim, weather_frame):
    """Asking for zero generators must not crash the model."""
    res = sim.simulate(
        weather_frame.tail(72), ScenarioType.NORMAL_OPERATION, 60, 45_000,
        duration_h=24, overrides=ScenarioOverrides(generators_available=0),
    )
    assert all(t["generator_kw"] == 0 for t in res.timeline)


def test_overrides_are_reported_back(sim, weather_frame):
    res = sim.simulate(
        weather_frame.tail(72), ScenarioType.LOW_SOLAR, 60, 45_000,
        duration_h=24, overrides=ScenarioOverrides(temperature_c=-50.0),
    )
    assert res.overrides_applied.get("temperature_c") == -50.0


def test_simulation_never_mutates_its_input(sim, weather_frame):
    before = weather_frame.tail(72).copy()
    sim.simulate(weather_frame.tail(72), ScenarioType.SEVERE_STORM,
                 60, 45_000, duration_h=48)
    after = weather_frame.tail(72)
    assert before["wind_speed_ms"].equals(after["wind_speed_ms"])
    assert before["temperature_c"].equals(after["temperature_c"])


def test_failed_scenario_does_not_report_inflated_autonomy(sim, weather_frame):
    """A station that ran out must not show a rosy autonomy figure.

    Shedding cuts demand, which would otherwise INCREASE the computed
    autonomy at the very moment supply failed.
    """
    res = sim.simulate(
        weather_frame.tail(72), ScenarioType.COMBINED_CRISIS, 60, 45_000,
        duration_h=48,
        overrides=ScenarioOverrides(initial_soc_pct=16, initial_fuel_l=400,
                                    generators_available=0),
    )
    if res.critical_load_secured:
        pytest.skip("scenario did not actually fail")
    assert res.severity_rating == "CATASTROPHIC"
    assert res.survival_hours < 48
    assert "NOT SECURED" in res.summary


def test_all_seven_required_scenarios_exist():
    required = {
        "NORMAL_OPERATION", "SEVERE_STORM", "LOW_SOLAR", "WIND_FAILURE",
        "GENERATOR_FAILURE", "HIGH_DEMAND", "COMBINED_CRISIS",
    }
    assert {s.value for s in SCENARIOS} == required
