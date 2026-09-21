"""Renewable generation prediction - hybrid physics + machine learning.

A pure ML model trained on limited data extrapolates badly at the tails, and
the tails are exactly where a polar station gets into trouble. So POLARIS
predicts in two stages:

    1. a deterministic physical baseline (turbine power curve with air-density
       correction; PV with plane-of-array transposition and cell-temperature
       derating)
    2. a scikit-learn residual model that learns the systematic error of that
       baseline from real weather features

Final prediction = physical baseline + learned correction. The two parts are
stored separately so the Explainable AI page can show how much of the answer
came from physics and how much from the learned correction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score

from app.config import settings
from app.core.station import SOLAR, WIND
from app.ml.features import RENEWABLE_FEATURES, build_features, select_matrix
from app.services.energy_model import compute_generation

log = logging.getLogger("polaris.ml.renewable")

MODEL_VERSION = "renewable-hybrid-1.0.0"
MODEL_FILE = "renewable_forecaster.joblib"


@dataclass
class RenewableTrainingReport:
    model_version: str
    n_samples: int
    trained_at: datetime
    wind_mae_kw: float
    solar_mae_kw: float
    wind_r2: float
    solar_r2: float
    wind_residual_std: float
    solar_residual_std: float
    feature_names: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "model_version": self.model_version,
            "model_type": "Physics baseline + RandomForest residual correction",
            "n_samples": self.n_samples,
            "trained_at": self.trained_at.isoformat(),
            "wind_mae_kw": round(self.wind_mae_kw, 4),
            "solar_mae_kw": round(self.solar_mae_kw, 4),
            "wind_r2": round(self.wind_r2, 5),
            "solar_r2": round(self.solar_r2, 5),
            "wind_residual_std_kw": round(self.wind_residual_std, 4),
            "solar_residual_std_kw": round(self.solar_residual_std, 4),
            "feature_names": self.feature_names,
        }


class RenewableForecaster:
    def __init__(self) -> None:
        self.wind_residual_model: RandomForestRegressor | None = None
        self.solar_residual_model: RandomForestRegressor | None = None
        self.feature_names: list[str] = list(RENEWABLE_FEATURES)
        self.wind_residual_std = 0.0
        self.solar_residual_std = 0.0
        self.report: RenewableTrainingReport | None = None

    # -- physics baseline --------------------------------------------------

    @staticmethod
    def physical_baseline(feats: pd.DataFrame) -> pd.DataFrame:
        """Deterministic generation estimate for every row."""
        wind_kw, solar_kw, icing, curtailed = [], [], [], []
        for r in feats.itertuples():
            gen = compute_generation(
                ts=r.observed_at.to_pydatetime(),
                temperature_c=float(r.temperature_c),
                wind_speed_ms=float(r.wind_speed_ms),
                solar_radiation_wm2=(
                    None if pd.isna(getattr(r, "solar_radiation_wm2", np.nan))
                    else float(r.solar_radiation_wm2)
                ),
                humidity_pct=(
                    None if pd.isna(getattr(r, "humidity_pct", np.nan))
                    else float(r.humidity_pct)
                ),
                cloud_cover_pct=(
                    None if pd.isna(getattr(r, "cloud_cover_pct", np.nan))
                    else float(r.cloud_cover_pct)
                ),
                air_density_kg_m3=(
                    None if pd.isna(getattr(r, "air_density_kg_m3", np.nan))
                    else float(r.air_density_kg_m3)
                ),
                direct_radiation_wm2=(
                    None if pd.isna(getattr(r, "direct_radiation_wm2", np.nan))
                    else float(getattr(r, "direct_radiation_wm2"))
                ),
                diffuse_radiation_wm2=(
                    None if pd.isna(getattr(r, "diffuse_radiation_wm2", np.nan))
                    else float(getattr(r, "diffuse_radiation_wm2"))
                ),
                snowfall_mm=(
                    None if pd.isna(getattr(r, "snowfall_mm", np.nan))
                    else float(getattr(r, "snowfall_mm"))
                ),
            )
            wind_kw.append(gen.wind_kw)
            solar_kw.append(gen.solar_kw)
            icing.append(bool(gen.wind_detail.get("icing_risk")))
            curtailed.append(bool(gen.wind_detail.get("curtailed")))

        return pd.DataFrame({
            "wind_physical_kw": wind_kw,
            "solar_physical_kw": solar_kw,
            "icing_risk": icing,
            "turbine_curtailed": curtailed,
        }, index=feats.index)

    # -- training ----------------------------------------------------------

    def fit(self, weather: pd.DataFrame) -> RenewableTrainingReport:
        """Train the residual correctors.

        The 'truth' here is the physics model evaluated on the *fully
        observed* weather record. The residual models learn how the baseline
        behaves when inputs are noisy, lagged or partially missing - which is
        what happens at forecast time.
        """
        if len(weather) < 48:
            raise ValueError("Need at least 48 hourly observations to train")

        feats = build_features(weather)
        base = self.physical_baseline(feats)
        X = select_matrix(feats, self.feature_names)

        # Reference truth: physics on the smoothed/actual record.
        y_wind = base["wind_physical_kw"].to_numpy(dtype=float)
        y_solar = base["solar_physical_kw"].to_numpy(dtype=float)

        # Baseline as seen through a 1-hour-persistence view of the weather,
        # which is the realistic information set at forecast time.
        lagged = feats.copy()
        for c in ("wind_speed_ms", "temperature_c", "solar_radiation_wm2",
                  "cloud_cover_pct", "humidity_pct"):
            if c in lagged.columns:
                lagged[c] = lagged[c].shift(1).bfill()
        lagged_base = self.physical_baseline(lagged)

        wind_resid = y_wind - lagged_base["wind_physical_kw"].to_numpy(dtype=float)
        solar_resid = y_solar - lagged_base["solar_physical_kw"].to_numpy(dtype=float)

        params = dict(
            n_estimators=220, max_depth=12, min_samples_leaf=4,
            random_state=settings.random_seed, n_jobs=-1,
        )
        self.wind_residual_model = RandomForestRegressor(**params)
        self.solar_residual_model = RandomForestRegressor(**params)
        self.wind_residual_model.fit(X, wind_resid)
        self.solar_residual_model.fit(X, solar_resid)

        wind_pred = (lagged_base["wind_physical_kw"].to_numpy()
                     + self.wind_residual_model.predict(X))
        solar_pred = (lagged_base["solar_physical_kw"].to_numpy()
                      + self.solar_residual_model.predict(X))
        wind_pred = np.clip(wind_pred, 0.0, WIND.rated_kw)
        solar_pred = np.clip(solar_pred, 0.0, SOLAR.rated_kwp)

        self.wind_residual_std = float(np.std(y_wind - wind_pred))
        self.solar_residual_std = float(np.std(y_solar - solar_pred))

        self.report = RenewableTrainingReport(
            model_version=MODEL_VERSION,
            n_samples=len(X),
            trained_at=datetime.now(tz=weather["observed_at"].iloc[0].tz),
            wind_mae_kw=float(mean_absolute_error(y_wind, wind_pred)),
            solar_mae_kw=float(mean_absolute_error(y_solar, solar_pred)),
            wind_r2=float(r2_score(y_wind, wind_pred)) if np.std(y_wind) > 0 else 0.0,
            solar_r2=float(r2_score(y_solar, solar_pred)) if np.std(y_solar) > 0 else 0.0,
            wind_residual_std=self.wind_residual_std,
            solar_residual_std=self.solar_residual_std,
            feature_names=list(X.columns),
        )
        log.info("RenewableForecaster trained: %s", self.report.as_dict())
        return self.report

    # -- inference ---------------------------------------------------------

    def predict(self, weather: pd.DataFrame) -> pd.DataFrame:
        feats = build_features(weather)
        base = self.physical_baseline(feats)
        X = select_matrix(feats, self.feature_names)

        wind_phys = base["wind_physical_kw"].to_numpy(dtype=float)
        solar_phys = base["solar_physical_kw"].to_numpy(dtype=float)

        if self.wind_residual_model is not None:
            wind_corr = self.wind_residual_model.predict(X)
            solar_corr = self.solar_residual_model.predict(X)
        else:  # physics-only fallback before the first training run
            wind_corr = np.zeros(len(X))
            solar_corr = np.zeros(len(X))

        wind = np.clip(wind_phys + wind_corr, 0.0, WIND.rated_kw)
        solar = np.clip(solar_phys + solar_corr, 0.0, SOLAR.rated_kwp)

        z = 1.2816
        return pd.DataFrame({
            "target_time": feats["observed_at"],
            "wind_kw": np.round(wind, 3),
            "solar_kw": np.round(solar, 3),
            "total_kw": np.round(wind + solar, 3),
            "wind_physical_kw": np.round(wind_phys, 3),
            "solar_physical_kw": np.round(solar_phys, 3),
            "ml_correction_kw": np.round(wind_corr + solar_corr, 3),
            "wind_kw_p10": np.round(np.clip(wind - z * self.wind_residual_std, 0, None), 3),
            "wind_kw_p90": np.round(np.clip(wind + z * self.wind_residual_std, 0, WIND.rated_kw), 3),
            "solar_kw_p10": np.round(np.clip(solar - z * self.solar_residual_std, 0, None), 3),
            "solar_kw_p90": np.round(np.clip(solar + z * self.solar_residual_std, 0, SOLAR.rated_kwp), 3),
            "icing_risk": base["icing_risk"].to_numpy(),
            "turbine_curtailed": base["turbine_curtailed"].to_numpy(),
            "input_temperature_c": feats["temperature_c"].to_numpy(),
            "input_wind_speed_ms": feats["wind_speed_ms"].to_numpy(),
            "input_solar_radiation_wm2": feats["solar_radiation_wm2"].to_numpy(),
            "model_version": MODEL_VERSION,
        })

    # -- persistence -------------------------------------------------------

    def save(self, path: str | None = None) -> str:
        target = path or f"{settings.model_dir}/{MODEL_FILE}"
        joblib.dump({
            "wind_residual_model": self.wind_residual_model,
            "solar_residual_model": self.solar_residual_model,
            "feature_names": self.feature_names,
            "wind_residual_std": self.wind_residual_std,
            "solar_residual_std": self.solar_residual_std,
            "report": self.report.as_dict() if self.report else None,
            "version": MODEL_VERSION,
        }, target)
        return target

    @classmethod
    def load(cls, path: str | None = None) -> "RenewableForecaster":
        target = path or f"{settings.model_dir}/{MODEL_FILE}"
        blob = joblib.load(target)
        inst = cls()
        inst.wind_residual_model = blob["wind_residual_model"]
        inst.solar_residual_model = blob["solar_residual_model"]
        inst.feature_names = blob["feature_names"]
        inst.wind_residual_std = blob["wind_residual_std"]
        inst.solar_residual_std = blob["solar_residual_std"]
        return inst
