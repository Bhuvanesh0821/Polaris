"""MET Norway forecast failover, and the radiation gap it exposes.

Uses a canned Locationforecast payload - no network - so these run anywhere.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pandas as pd
import pytest

from app.models.base import DataProvenance
from app.services.weather.providers import (
    MetNorwayProvider,
    _interp_direction,
    _station_pressure,
)


def _payload(start: datetime) -> dict:
    """3 hourly steps, then two 6-hourly steps - the real service's shape."""
    times = [start + timedelta(hours=h) for h in (0, 1, 2, 8, 14)]
    dirs = [350.0, 355.0, 358.0, 10.0, 20.0]
    series = [{
        "time": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": {"instant": {"details": {
            "air_temperature": -20.0 + i, "apparent_air_temperature": -30.0 + i,
            "wind_speed": 6.0 + i, "wind_from_direction": dirs[i],
            "relative_humidity": 60.0, "cloud_area_fraction": 40.0,
            "air_pressure_at_sea_level": 970.0,
        }}},
    } for i, t in enumerate(times)]
    return {"properties": {"meta": {"updated_at": "2026-09-23T12:00:00Z"},
                           "timeseries": series}}


def _fetch(payload: dict, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await MetNorwayProvider()._fetch(c)

    readings, status, _ = asyncio.run(run())
    return readings


@pytest.fixture
def now_hour():
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def test_emits_only_future_forecast_hours(now_hour):
    """The current hour is a model value, not an observation."""
    rows = _fetch(_payload(now_hour))
    assert rows
    assert all(r.observed_at > now_hour for r in rows)
    assert {r.provenance for r in rows} == {DataProvenance.REAL_FORECAST}


def test_six_hourly_tail_becomes_hourly_and_is_flagged(now_hour):
    rows = _fetch(_payload(now_hour))
    times = [r.observed_at for r in rows]
    assert times == [now_hour + timedelta(hours=h) for h in range(1, 15)]

    by_hour = {int((r.observed_at - now_hour).total_seconds() // 3600): r for r in rows}
    assert by_hour[8].raw_payload["interpolated"] is False      # a real model step
    assert by_hour[5].raw_payload["interpolated"] is True       # filled between 2 and 8
    # halfway between the steps at +2 h (-18 C) and +8 h (-17 C)
    assert by_hour[5].temperature_c == pytest.approx(-17.5)


def test_wind_direction_interpolates_across_north():
    """350 -> 10 must pass through 0, not swing back through 180."""
    assert _interp_direction(350.0, 10.0, 0.5) == pytest.approx(0.0, abs=0.01)
    assert _interp_direction(10.0, 350.0, 0.25) == pytest.approx(5.0)


def test_pressure_is_reduced_to_station_level():
    p = _station_pressure(970.0, -20.0, 117.0)
    assert 950.0 < p < 960.0                   # ~15 hPa lower at 117 m
    assert _station_pressure(None, -20.0, 117.0) is None


def test_identifies_itself_as_the_service_requires(now_hour):
    seen: list[httpx.Request] = []
    _fetch(_payload(now_hour), seen)
    assert "POLARIS" in seen[0].headers["user-agent"]


def test_missing_radiation_is_estimated_not_zero():
    """A forecast without radiation must not tell the models it is dark."""
    from app.ml.features import build_features

    noon = datetime(2026, 12, 15, 11, 0, tzinfo=timezone.utc)   # polar day
    frame = pd.DataFrame({
        "observed_at": [noon, noon + timedelta(hours=1)],
        "temperature_c": [-5.0, -5.0], "wind_speed_ms": [5.0, 5.0],
        "solar_radiation_wm2": [np.nan, 300.0],
        "cloud_cover_pct": [20.0, 20.0], "humidity_pct": [60.0, 60.0],
        "pressure_hpa": [955.0, 955.0],
    })
    out = build_features(frame)
    assert out.loc[0, "solar_radiation_wm2"] > 100.0
    assert bool(out.loc[0, "solar_radiation_estimated"]) is True
    assert out.loc[1, "solar_radiation_wm2"] == pytest.approx(300.0)
    assert bool(out.loc[1, "solar_radiation_estimated"]) is False
