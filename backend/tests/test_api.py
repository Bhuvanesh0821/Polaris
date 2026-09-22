"""API + database integration tests.

These run against a live backend on :8000. They skip (rather than fail) when
one is not running, so the physics/ML suite stays useful on its own.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client(api_base, api_up):
    if not api_up:
        pytest.skip("backend not running on :8000")
    return httpx.Client(base_url=api_base, timeout=120.0)


# -------------------------------------------------------------- health ----

def test_health_reports_database_connected(client):
    r = client.get("/health")
    assert r.status_code == 200
    d = r.json()
    assert d["database_connected"] is True
    assert d["status"] in ("ok", "degraded")
    assert "PostgreSQL" in (d["database_version"] or "")


def test_health_never_leaks_the_password(client):
    dsn = client.get("/health").json()["database_dsn"]
    assert "***" in dsn


# ------------------------------------------------------------ endpoints ---

REQUIRED_GETS = [
    "/weather", "/weather/current", "/weather/sources", "/weather/providers",
    "/weather/stations", "/energy/status", "/energy/history",
    "/energy/load-profile", "/load/forecast", "/renewable/forecast",
    "/survival-analysis", "/recommendations", "/alerts", "/explainability",
    "/dashboard/summary", "/model/info", "/crisis/scenarios",
    "/optimization/latest", "/scheduler/status",
]


@pytest.mark.parametrize("path", REQUIRED_GETS)
def test_endpoint_returns_200(client, path):
    assert client.get(path).status_code == 200


def test_optimization_run(client):
    r = client.post("/optimization/run",
                    json={"horizon_h": 24, "allow_deferral": True})
    assert r.status_code == 200
    d = r.json()["data"]
    assert len(d["schedule"]) == 24
    assert d["fuel_saved_l"] >= -1e-6
    assert d["rationale"]


def test_crisis_simulate(client):
    r = client.post("/crisis/simulate",
                    json={"scenario": "SEVERE_STORM", "duration_h": 24})
    assert r.status_code == 200
    d = r.json()["data"]
    assert "NOT LIVE" in d["disclaimer"]
    assert d["before"] and d["after"]
    assert d["recommendation"]["reasons"]


# --------------------------------------------------------- data honesty ---

def test_weather_is_labelled_real(client):
    env = client.get("/weather/current").json()
    assert env["provenance"]["data_class"] == "REAL_LIVE_WEATHER"
    assert env["provenance"]["is_measured"] is True


def test_energy_is_labelled_modelled_not_measured(client):
    env = client.get("/energy/status").json()
    p = env["provenance"]
    assert p["data_class"] == "MODELLED_ENERGY"
    assert p["is_measured"] is False
    assert p["is_live"] is False
    assert "modelled" in (p["disclaimer"] or "").lower()


def test_crisis_output_is_labelled_simulated(client):
    env = client.post("/crisis/simulate",
                      json={"scenario": "LOW_SOLAR", "duration_h": 12}).json()
    assert env["provenance"]["data_class"] == "SIMULATED_SCENARIO"


def test_no_endpoint_claims_live_station_telemetry(client):
    """The whole project rests on not making this claim."""
    for path in ("/energy/status", "/survival-analysis", "/dashboard/summary"):
        body = client.get(path).text.lower()
        assert "live telemetry" not in body
        assert "real-time telemetry from" not in body


def test_weather_rows_carry_source_attribution(client):
    rows = client.get("/weather", params={"hours": 24}).json()["data"]
    assert rows
    for row in rows:
        assert row["source"]
        assert row["source_provider"]
        assert row["provenance"] in (
            "LIVE_OBSERVED", "REAL_OBSERVED", "REAL_FORECAST")


def test_current_weather_attributes_each_field(client):
    d = client.get("/weather/current").json()["data"]
    temp = d["fields"]["temperature_c"]
    assert temp["value"] is not None
    assert temp["source"]
    assert d["status"] in ("LIVE", "STALE", "FAILED")


# ------------------------------------------------- dashboard completeness --

def test_dashboard_has_every_required_section(client):
    d = client.get("/dashboard/summary").json()
    for key in ("station", "live_weather", "energy", "survival",
                "active_alerts", "data_sources", "scheduler", "banners"):
        assert key in d, f"dashboard is missing {key}"


def test_dashboard_energy_values_are_populated(client):
    e = client.get("/dashboard/summary").json()["energy"]
    if e is None:
        pytest.skip("no modelled state yet")
    for key in ("total_load_kw", "critical_load_kw", "battery_soc_pct",
                "fuel_level_l", "renewable_kw", "generator_output_kw"):
        assert e[key] is not None


def test_dashboard_carries_risk_level(client):
    s = client.get("/dashboard/summary").json()["survival"]
    if s is None:
        pytest.skip("no modelled state yet")
    assert s["risk"]["level"] in ("LOW", "MODERATE", "ELEVATED", "HIGH", "SEVERE")
    assert s["risk"]["factors"]


def test_dashboard_carries_three_autonomy_modes(client):
    s = client.get("/dashboard/summary").json()["survival"]
    if s is None:
        pytest.skip("no modelled state yet")
    m = s["modes"]
    assert m["normal"] and m["critical_only"] and m["crisis"]


# ------------------------------------------------------------ forecasts ---

def _forecast_or_skip(client, path, hours):
    """Fetch a forecast, or skip when none is legitimately available.

    A 404 here is a valid, honest state: it means the NWP provider is
    unreachable and POLARIS refused to invent a forecast. These tests check
    the SHAPE of a forecast, so they have nothing to assert in that case.
    test_forecast_rows_are_genuinely_in_the_future covers the 404 path.
    """
    r = client.get(path, params={"hours": hours})
    if r.status_code == 404:
        pytest.skip("no forecast available (NWP provider unreachable)")
    assert r.status_code == 200
    return r.json()


@pytest.mark.parametrize("hours", [6, 24, 48])
def test_forecast_horizons(client, hours):
    load = _forecast_or_skip(client, "/load/forecast", hours)["data"]
    renew = _forecast_or_skip(client, "/renewable/forecast", hours)["data"]
    assert len(load) == hours
    assert len(renew) == hours


def test_seven_day_forecast_is_available(client):
    d = _forecast_or_skip(client, "/load/forecast", 168)["data"]
    # NWP providers cap out around 6-7 days; anything past 5 days is enough
    assert len(d) > 120


def test_forecast_is_labelled_ai(client):
    env = _forecast_or_skip(client, "/load/forecast", 6)
    assert env["provenance"]["data_class"] == "AI_FORECAST"


def test_renewable_forecast_splits_wind_and_solar(client):
    rows = _forecast_or_skip(client, "/renewable/forecast", 24)["data"]
    for r in rows:
        assert r["total_kw"] == pytest.approx(r["wind_kw"] + r["solar_kw"], abs=0.05)


# ------------------------------------------------------------ decisions ---

def test_recommendations_explain_themselves(client):
    recs = client.get("/recommendations").json()["data"]
    if not recs:
        pytest.skip("no active recommendations")
    for r in recs:
        assert r["title"] and r["action"] and r["rationale"]
        assert r["reasons"], "every recommendation needs WHY THIS ACTION reasons"


def test_explainability_returns_attribution(client):
    r = client.get("/explainability")
    if r.status_code == 503:
        pytest.skip("models not trained")
    d = r.json()["data"]
    assert d["narrative"]
    assert d["contributions"]
    assert d["global_importance"]


def test_validation_rejects_bad_input(client):
    assert client.post("/crisis/simulate",
                       json={"scenario": "NOT_A_SCENARIO"}).status_code == 422
    assert client.post("/optimization/run",
                       json={"horizon_h": 9999}).status_code == 422
    assert client.get("/alerts", params={"status": "BOGUS"}).status_code == 422


# ------------------------------------------------ forecast honesty --------

def test_forecast_rows_are_genuinely_in_the_future(client):
    """A forecast must never be past weather relabelled.

    Regression guard. When the NWP provider was rate-limited in production,
    the pipeline silently fell back to replaying observed history and served
    it as a 7-day forecast - every target_time was in the past. POLARIS must
    return no forecast at all rather than a dishonest one.
    """
    import datetime as _dt

    r = client.get("/load/forecast", params={"hours": 48})
    if r.status_code == 404:
        # Honest unavailability is an acceptable outcome; the message must
        # explain why rather than pretending the data is merely missing.
        assert "forward-looking" in r.json()["detail"].lower()
        return

    rows = r.json()["data"]
    assert rows
    now = _dt.datetime.now(_dt.timezone.utc)
    cutoff = now - _dt.timedelta(hours=2)   # allow the current hour to count
    stale = [
        x["target_time"] for x in rows
        if _dt.datetime.fromisoformat(
            x["target_time"].replace("Z", "+00:00")) < cutoff
    ]
    assert not stale, (
        f"{len(stale)}/{len(rows)} forecast rows are in the past - "
        f"past weather is being presented as a forecast (first: {stale[:1]})"
    )


def test_renewable_forecast_is_also_forward_looking(client):
    import datetime as _dt

    r = client.get("/renewable/forecast", params={"hours": 24})
    if r.status_code == 404:
        assert "forward-looking" in r.json()["detail"].lower()
        return
    rows = r.json()["data"]
    now = _dt.datetime.now(_dt.timezone.utc)
    cutoff = now - _dt.timedelta(hours=2)
    stale = [
        x for x in rows
        if _dt.datetime.fromisoformat(
            x["target_time"].replace("Z", "+00:00")) < cutoff
    ]
    assert not stale, f"{len(stale)}/{len(rows)} renewable forecast rows are stale"
