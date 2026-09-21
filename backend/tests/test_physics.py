"""Physics core: solar geometry, turbine curve, fuel model, battery."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core import physics
from app.core.station import BATTERY, FUEL, GENERATOR, SITE, WIND


# --------------------------------------------------------------- solar ----

def test_polar_night_in_midwinter():
    """At 70.77 S the sun stays below the horizon around the June solstice."""
    assert physics.is_polar_night(datetime(2026, 6, 21, tzinfo=timezone.utc))


def test_midsummer_is_not_polar_night():
    assert not physics.is_polar_night(datetime(2026, 12, 21, tzinfo=timezone.utc))


def test_solstice_max_elevation_matches_geometry():
    """Noon elevation at the summer solstice = 90 - |lat| + 23.45."""
    day = datetime(2026, 12, 21, tzinfo=timezone.utc)
    best = max(physics.solar_position(day.replace(hour=h)).elevation_deg
               for h in range(24))
    expected = 90.0 - abs(SITE.latitude) + 23.45
    assert best == pytest.approx(expected, abs=1.0)


def test_sun_is_north_at_southern_hemisphere_noon():
    """Southern hemisphere: the sun is to the NORTH at local solar noon."""
    day = datetime(2026, 12, 21, tzinfo=timezone.utc)
    best = max((physics.solar_position(day.replace(hour=h)) for h in range(24)),
               key=lambda p: p.elevation_deg)
    # azimuth measured clockwise from north
    assert best.azimuth_deg < 45 or best.azimuth_deg > 315


def test_no_solar_output_without_irradiance():
    kw, _ = physics.solar_power_kw(
        datetime(2026, 6, 21, 12, tzinfo=timezone.utc), ghi=0.0, temp_c=-30
    )
    assert kw == 0.0


# ---------------------------------------------------------------- wind ----

def test_turbine_below_cut_in_produces_nothing():
    kw, d = physics.wind_power_kw(1.0, temp_c=-25, humidity_pct=50)
    assert kw == 0.0
    assert "cut-in" in d["reason"]


def test_turbine_storm_cut_out():
    """Above cut-out the array feathers and output is zero, not maximal."""
    kw, d = physics.wind_power_kw(30.0, temp_c=-25, humidity_pct=50)
    assert kw == 0.0
    assert d["curtailed"] is True


def test_turbine_power_is_monotonic_up_to_rated():
    prev = -1.0
    for v in [3.5, 5, 7, 9, 11, 12]:
        kw, _ = physics.wind_power_kw(v, temp_c=-25, humidity_pct=50)
        assert kw >= prev
        prev = kw


def test_cold_air_density_increases_output():
    """Colder, denser polar air yields more power at the same wind speed."""
    warm = physics.air_density(0.0, 985.0)
    cold = physics.air_density(-40.0, 985.0)
    assert cold > warm
    kw_warm, _ = physics.wind_power_kw(8.0, air_density_kg_m3=warm, temp_c=0)
    kw_cold, _ = physics.wind_power_kw(8.0, air_density_kg_m3=cold, temp_c=-40)
    assert kw_cold > kw_warm


def test_output_never_exceeds_nameplate():
    kw, _ = physics.wind_power_kw(20.0, air_density_kg_m3=1.6, temp_c=-50)
    assert kw <= WIND.rated_kw + 1e-6


# ---------------------------------------------------------------- fuel ----

def test_specific_fuel_consumption_improves_with_loading():
    """A lightly loaded genset wastes fuel - that is why wet stacking matters."""
    light = physics.genset_fuel_lph(30, 100) / 30
    heavy = physics.genset_fuel_lph(80, 100) / 80
    assert light > heavy


def test_genset_efficiency_is_physically_plausible():
    eff = physics.genset_efficiency(80, 100)
    assert 0.25 < eff < 0.45


def test_dispatch_prefers_fewer_units_at_higher_loading():
    d = physics.dispatch_gensets(80.0, -25.0, units_available=3)
    assert d["units_running"] == 1
    assert d["loading_pct"] > GENERATOR.min_loading_frac * 100


def test_dispatch_flags_wet_stacking():
    d = physics.dispatch_gensets(20.0, -25.0, units_available=3)
    assert d["wet_stacking"] is True


def test_dispatch_reports_unmet_load_when_capacity_short():
    d = physics.dispatch_gensets(1000.0, -25.0, units_available=1)
    assert d["unmet_kw"] > 0


def test_fuel_energy_density_is_realistic():
    assert 9.0 < FUEL.energy_kwh_per_l < 11.0


# ------------------------------------------------------------- battery ----

def test_battery_never_discharges_below_floor():
    step = physics.battery_step(BATTERY.soc_min_pct + 0.5, -500.0, 1.0, -25.0)
    assert step.soc_pct >= BATTERY.soc_min_pct - 0.5


def test_battery_never_charges_above_ceiling():
    step = physics.battery_step(BATTERY.soc_max_pct - 0.5, 500.0, 1.0, -25.0)
    assert step.soc_pct <= BATTERY.soc_max_pct + 0.5


def test_cold_reduces_usable_capacity():
    warm = physics.battery_temp_derate(20.0)
    cold = physics.battery_temp_derate(-10.0)
    assert cold < warm
    assert cold >= BATTERY.temp_derate_floor


def test_round_trip_loses_energy():
    """Charging then discharging the same power must not create energy."""
    start = 50.0
    up = physics.battery_step(start, 50.0, 1.0, -20.0)
    down = physics.battery_step(up.soc_pct, -50.0, 1.0, -20.0)
    assert down.soc_pct < start


def test_wind_chill_is_colder_than_dry_bulb():
    assert physics.wind_chill_c(-20.0, 15.0) < -20.0


def test_wind_chill_ignored_in_still_air():
    assert physics.wind_chill_c(-20.0, 0.0) == pytest.approx(-20.0, abs=0.01)
