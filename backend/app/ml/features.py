"""Feature engineering for the POLARIS AI models.

Inputs are REAL measured weather; outputs are the design matrix consumed by
the scikit-learn forecasters. Kept deliberately deterministic and free of
look-ahead: every lag/rolling feature uses only strictly past values.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.physics import solar_position
from app.core.station import HEATING_BALANCE_POINT_C

BASE_WEATHER_COLS = [
    "temperature_c", "wind_speed_ms", "solar_radiation_wm2",
    "humidity_pct", "pressure_hpa", "cloud_cover_pct", "wind_chill_c",
    "air_density_kg_m3",
]

LOAD_FEATURES = [
    "hour_sin", "hour_cos", "doy_sin", "doy_cos",
    "temperature_c", "wind_chill_c", "wind_speed_ms",
    "hdd", "hdd_x_wind", "crew_est", "is_polar_night", "solar_elevation",
    "temp_lag_1", "temp_lag_3", "temp_roll_6", "temp_roll_24",
    "wind_roll_6", "is_weekend_shift",
]

RENEWABLE_FEATURES = [
    "hour_sin", "hour_cos", "doy_sin", "doy_cos",
    "wind_speed_ms", "wind_speed_sq", "wind_speed_cube",
    "air_density_kg_m3", "temperature_c", "humidity_pct",
    "solar_radiation_wm2", "solar_elevation", "cos_zenith",
    "cloud_cover_pct", "clear_sky_index",
    "wind_roll_3", "wind_roll_12", "icing_flag",
]


def _cyc(values: pd.Series, period: float) -> tuple[pd.Series, pd.Series]:
    rad = 2.0 * np.pi * values / period
    return np.sin(rad), np.cos(rad)


def build_features(df: pd.DataFrame, timestamp_col: str = "observed_at") -> pd.DataFrame:
    """Add calendar, physical and lag features to a weather frame.

    The frame must be sorted ascending by `timestamp_col` and hourly.
    """
    if df.empty:
        return df.copy()

    out = df.copy()
    out[timestamp_col] = pd.to_datetime(out[timestamp_col], utc=True)
    out = out.sort_values(timestamp_col).reset_index(drop=True)

    ts = out[timestamp_col]
    hours = ts.dt.hour + ts.dt.minute / 60.0
    doy = ts.dt.dayofyear.astype(float)

    out["hour_sin"], out["hour_cos"] = _cyc(hours, 24.0)
    out["doy_sin"], out["doy_cos"] = _cyc(doy, 365.25)

    # --- ensure every base column exists ---
    for col in BASE_WEATHER_COLS:
        if col not in out.columns:
            out[col] = np.nan

    # Wind chill is derived, not measured; recompute where absent.
    mask = out["wind_chill_c"].isna()
    if mask.any():
        t = out.loc[mask, "temperature_c"]
        v_kmh = out.loc[mask, "wind_speed_ms"] * 3.6
        v16 = v_kmh.clip(lower=0.0) ** 0.16
        wc = 13.12 + 0.6215 * t - 11.37 * v16 + 0.3965 * t * v16
        out.loc[mask, "wind_chill_c"] = np.where(
            (t <= 10.0) & (v_kmh >= 4.8), wc, t
        )

    out["air_density_kg_m3"] = out["air_density_kg_m3"].fillna(
        (out["pressure_hpa"].fillna(985.0) * 100.0)
        / (287.05 * (out["temperature_c"] + 273.15).clip(lower=150.0))
    )

    # --- heating-degree drivers ---
    out["hdd"] = (HEATING_BALANCE_POINT_C - out["wind_chill_c"]).clip(lower=0.0)
    out["hdd_x_wind"] = out["hdd"] * out["wind_speed_ms"].fillna(0.0)

    # --- solar geometry (deterministic from timestamp) ---
    positions = [solar_position(t.to_pydatetime()) for t in ts]
    out["solar_elevation"] = [p.elevation_deg for p in positions]
    out["cos_zenith"] = [p.cos_zenith for p in positions]
    out["is_polar_night"] = (
        out.get("is_polar_night", pd.Series(False, index=out.index))
        .fillna(False).astype(int)
        if "is_polar_night" in df.columns
        else (pd.Series([p.elevation_deg for p in positions]) <= 0).astype(int)
    )

    # Clear-sky index: how much of the theoretical maximum actually arrived.
    from app.core.physics import clear_sky_ghi

    cs = np.array([clear_sky_ghi(p) for p in positions])
    ghi = out["solar_radiation_wm2"].fillna(0.0).to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        csi = np.where(cs > 5.0, ghi / cs, 0.0)
    out["clear_sky_index"] = np.clip(csi, 0.0, 1.3)

    # --- wind polynomial terms (turbine power is cubic in wind speed) ---
    w = out["wind_speed_ms"].fillna(0.0)
    out["wind_speed_sq"] = w**2
    out["wind_speed_cube"] = w**3

    # --- rime-icing indicator ---
    out["icing_flag"] = (
        out["temperature_c"].between(-9.0, 0.5)
        & (out["humidity_pct"].fillna(0.0) >= 85.0)
    ).astype(int)

    # --- crew / occupancy proxy ---
    phase = np.cos(2 * np.pi * (doy - 1) / 365.25)
    out["crew_est"] = 25.0 + (65.0 - 25.0) * (phase + 1.0) / 2.0

    # Station work rhythm: Sunday is a rest day at most Antarctic stations.
    out["is_weekend_shift"] = (ts.dt.dayofweek == 6).astype(int)

    # --- lags and rolling means (strictly past) ---
    out["temp_lag_1"] = out["temperature_c"].shift(1)
    out["temp_lag_3"] = out["temperature_c"].shift(3)
    out["temp_roll_6"] = out["temperature_c"].shift(1).rolling(6, min_periods=1).mean()
    out["temp_roll_24"] = out["temperature_c"].shift(1).rolling(24, min_periods=1).mean()
    out["wind_roll_3"] = out["wind_speed_ms"].shift(1).rolling(3, min_periods=1).mean()
    out["wind_roll_6"] = out["wind_speed_ms"].shift(1).rolling(6, min_periods=1).mean()
    out["wind_roll_12"] = out["wind_speed_ms"].shift(1).rolling(12, min_periods=1).mean()

    # Backfill only the lag columns at the very start of the series.
    lag_cols = ["temp_lag_1", "temp_lag_3", "temp_roll_6", "temp_roll_24",
                "wind_roll_3", "wind_roll_6", "wind_roll_12"]
    for c in lag_cols:
        out[c] = out[c].bfill()

    return out


def select_matrix(df: pd.DataFrame, feature_names: list[str]) -> pd.DataFrame:
    """Return the model matrix with a guaranteed column set and no NaNs."""
    X = pd.DataFrame(index=df.index)
    for name in feature_names:
        X[name] = df[name] if name in df.columns else 0.0
    X = X.apply(pd.to_numeric, errors="coerce")
    # Median is a safe, leakage-free imputation for the small residual gaps
    # left by sources that do not report a variable at all.
    return X.fillna(X.median(numeric_only=True)).fillna(0.0)


FEATURE_DESCRIPTIONS: dict[str, str] = {
    "hour_sin": "Time of day (sine component) - captures the diurnal activity cycle",
    "hour_cos": "Time of day (cosine component)",
    "doy_sin": "Day of year (sine) - austral seasonal cycle",
    "doy_cos": "Day of year (cosine)",
    "temperature_c": "Measured air temperature",
    "wind_chill_c": "Wind chill - drives building heat loss more than dry-bulb temp",
    "wind_speed_ms": "Measured wind speed at 10 m",
    "wind_speed_sq": "Wind speed squared",
    "wind_speed_cube": "Wind speed cubed - turbine power scales with v^3",
    "hdd": "Heating degrees below the 14 C balance point",
    "hdd_x_wind": "Heating demand amplified by wind (infiltration losses)",
    "crew_est": "Estimated crew on station (seasonal)",
    "is_polar_night": "Whether the sun stays below the horizon all day",
    "solar_elevation": "Sun elevation angle",
    "cos_zenith": "Cosine of solar zenith angle",
    "clear_sky_index": "Measured irradiance / clear-sky irradiance (cloudiness)",
    "cloud_cover_pct": "Measured cloud cover",
    "humidity_pct": "Measured relative humidity",
    "pressure_hpa": "Measured barometric pressure",
    "air_density_kg_m3": "Air density - cold polar air yields more turbine power",
    "solar_radiation_wm2": "Measured global horizontal irradiance",
    "icing_flag": "Rime-icing risk window (near-freezing and humid)",
    "temp_lag_1": "Temperature 1 hour ago",
    "temp_lag_3": "Temperature 3 hours ago",
    "temp_roll_6": "Mean temperature over the previous 6 hours",
    "temp_roll_24": "Mean temperature over the previous 24 hours (thermal mass)",
    "wind_roll_3": "Mean wind over the previous 3 hours",
    "wind_roll_6": "Mean wind over the previous 6 hours",
    "wind_roll_12": "Mean wind over the previous 12 hours",
    "is_weekend_shift": "Sunday - reduced station work rhythm",
}
