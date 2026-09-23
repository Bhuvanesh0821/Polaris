"""Shared fixtures for the POLARIS test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session")
def weather_frame() -> pd.DataFrame:
    """A deterministic hourly weather frame for model-level tests.

    This is TEST INPUT ONLY - the application itself never synthesises
    weather. Values are physically plausible for Maitri in winter so the
    physics assertions below are meaningful.
    """
    n = 24 * 20
    ts = pd.date_range("2026-06-01", periods=n, freq="h", tz="UTC")
    rng = np.random.default_rng(42)
    hours = ts.hour.to_numpy()
    temp = -25 + 5 * np.sin(2 * np.pi * hours / 24) + rng.normal(0, 2.5, n)
    wind = np.clip(8 + 5 * np.sin(2 * np.pi * np.arange(n) / 91)
                   + rng.gamma(2, 1.2, n), 0, 32)
    df = pd.DataFrame({
        "observed_at": ts,
        "temperature_c": temp,
        "wind_speed_ms": wind,
        "solar_radiation_wm2": np.zeros(n),          # polar night
        "humidity_pct": np.clip(rng.normal(72, 10, n), 15, 100),
        "pressure_hpa": rng.normal(985, 8, n),
        "cloud_cover_pct": rng.uniform(0, 100, n),
        "snowfall_mm": np.where(rng.random(n) < 0.08, rng.random(n) * 3, 0.0),
    })
    df["wind_chill_c"] = np.nan
    df["air_density_kg_m3"] = np.nan
    df["is_polar_night"] = True
    return df


@pytest.fixture(scope="session")
def summer_frame() -> pd.DataFrame:
    """Summer frame with real daylight, so solar paths are exercised."""
    n = 24 * 10
    ts = pd.date_range("2026-01-05", periods=n, freq="h", tz="UTC")
    rng = np.random.default_rng(7)
    from app.core.physics import ghi_from_cloud_cover, solar_position

    cloud = rng.uniform(10, 70, n)
    ghi = np.array([
        ghi_from_cloud_cover(solar_position(t.to_pydatetime()), c)
        for t, c in zip(ts, cloud)
    ])
    df = pd.DataFrame({
        "observed_at": ts,
        "temperature_c": -3 + 4 * np.sin(2 * np.pi * ts.hour.to_numpy() / 24),
        "wind_speed_ms": np.clip(rng.normal(7, 3, n), 0, 30),
        "solar_radiation_wm2": ghi,
        "humidity_pct": rng.uniform(40, 90, n),
        "pressure_hpa": rng.normal(988, 6, n),
        "cloud_cover_pct": cloud,
        "snowfall_mm": np.zeros(n),
    })
    df["wind_chill_c"] = np.nan
    df["air_density_kg_m3"] = np.nan
    df["is_polar_night"] = False
    return df


@pytest.fixture(scope="session")
def api_base() -> str:
    import os

    return os.environ.get("POLARIS_API_BASE", "http://127.0.0.1:8000/api")


@pytest.fixture(scope="session")
def api_up(api_base: str) -> bool:
    """True when a live POLARIS backend is reachable; API tests skip if not.

    Checks identity, not just a 200: another local project on the same port
    would otherwise have the whole API suite run against the wrong app.
    """
    import httpx

    try:
        r = httpx.get(f"{api_base}/ping", timeout=5.0)
        return r.status_code == 200 and r.json().get("app") == "POLARIS"
    except Exception:
        return False
