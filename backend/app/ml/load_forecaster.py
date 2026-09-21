"""AI load forecasting.

Learns the mapping  REAL weather -> MODELLED station demand, then applies it
to the live weather forecast to produce an AI FORECAST of station load.

The training target comes from the research-based energy model in
services/energy_model.py. That is stated plainly in the model card: the
forecaster learns a physically-grounded load model, it does not learn from
station telemetry, because no such public telemetry exists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import TimeSeriesSplit

from app.config import settings
from app.ml.features import LOAD_FEATURES, build_features, select_matrix
from app.services.energy_model import compute_load

log = logging.getLogger("polaris.ml.load")

MODEL_VERSION = "load-hgb-1.0.0"
MODEL_FILE = "load_forecaster.joblib"


@dataclass
class TrainingReport:
    model_version: str
    n_samples: int
    n_features: int
    trained_at: datetime
    mae_kw: float
    r2: float
    cv_mae_kw: float
    residual_std_kw: float
    feature_names: list[str] = field(default_factory=list)
    target_description: str = ""

    def as_dict(self) -> dict:
        return {
            "model_version": self.model_version,
            "model_type": "HistGradientBoostingRegressor",
            "n_samples": self.n_samples,
            "n_features": self.n_features,
            "trained_at": self.trained_at.isoformat(),
            "mae_kw": round(self.mae_kw, 4),
            "r2": round(self.r2, 5),
            "cv_mae_kw": round(self.cv_mae_kw, 4),
            "residual_std_kw": round(self.residual_std_kw, 4),
            "feature_names": self.feature_names,
            "target_description": self.target_description,
        }


class LoadForecaster:
    """Gradient-boosted regressor predicting total station load in kW."""

    TARGET_NOTE = (
        "Target is MODELLED station load from the POLARIS research-based "
        "energy model (no public station telemetry exists). Inputs are REAL "
        "measured weather."
    )

    def __init__(self) -> None:
        self.model: HistGradientBoostingRegressor | None = None
        self.critical_model: HistGradientBoostingRegressor | None = None
        self.feature_names: list[str] = list(LOAD_FEATURES)
        self.residual_std: float = 0.0
        self.report: TrainingReport | None = None

    # -- training ----------------------------------------------------------

    @staticmethod
    def build_targets(weather: pd.DataFrame) -> pd.DataFrame:
        """Run the physics load model over a real weather history."""
        rows = []
        for r in weather.itertuples():
            load = compute_load(
                ts=r.observed_at.to_pydatetime(),
                temperature_c=float(r.temperature_c),
                wind_chill_c=float(getattr(r, "wind_chill_c", r.temperature_c) or r.temperature_c),
                is_polar_night=bool(getattr(r, "is_polar_night", 0)),
            )
            rows.append({
                "observed_at": r.observed_at,
                "total_load_kw": load.total_kw,
                "critical_load_kw": load.critical_kw,
                "deferrable_load_kw": load.deferrable_kw,
                "heating_kw": load.heating_kw,
            })
        return pd.DataFrame(rows)

    def fit(self, weather: pd.DataFrame) -> TrainingReport:
        if len(weather) < 48:
            raise ValueError(
                f"Need at least 48 hourly observations to train, got {len(weather)}"
            )
        feats = build_features(weather)
        targets = self.build_targets(feats)
        data = feats.merge(targets, on="observed_at", how="inner")

        X = select_matrix(data, self.feature_names)
        y = data["total_load_kw"].to_numpy(dtype=float)
        y_crit = data["critical_load_kw"].to_numpy(dtype=float)

        params = dict(
            max_iter=320, learning_rate=0.06, max_depth=6,
            min_samples_leaf=12, l2_regularization=0.15,
            random_state=settings.random_seed,
        )
        self.model = HistGradientBoostingRegressor(**params)
        self.critical_model = HistGradientBoostingRegressor(**params)

        # Time-ordered CV - a random split would leak future into past.
        n_splits = max(2, min(5, len(X) // 200))
        cv_scores = []
        splitter = TimeSeriesSplit(n_splits=n_splits)
        for tr, te in splitter.split(X):
            m = HistGradientBoostingRegressor(**params)
            m.fit(X.iloc[tr], y[tr])
            cv_scores.append(mean_absolute_error(y[te], m.predict(X.iloc[te])))

        self.model.fit(X, y)
        self.critical_model.fit(X, y_crit)

        pred = self.model.predict(X)
        residuals = y - pred
        self.residual_std = float(np.std(residuals))

        self.report = TrainingReport(
            model_version=MODEL_VERSION,
            n_samples=len(X),
            n_features=X.shape[1],
            trained_at=datetime.now(tz=weather["observed_at"].iloc[0].tz),
            mae_kw=float(mean_absolute_error(y, pred)),
            r2=float(r2_score(y, pred)),
            cv_mae_kw=float(np.mean(cv_scores)) if cv_scores else float("nan"),
            residual_std_kw=self.residual_std,
            feature_names=list(X.columns),
            target_description=self.TARGET_NOTE,
        )
        log.info("LoadForecaster trained: %s", self.report.as_dict())
        return self.report

    # -- inference ---------------------------------------------------------

    def predict(self, weather: pd.DataFrame) -> pd.DataFrame:
        """Predict load for each row of a (live or forecast) weather frame."""
        if self.model is None or self.critical_model is None:
            raise RuntimeError("LoadForecaster is not trained")
        feats = build_features(weather)
        X = select_matrix(feats, self.feature_names)

        total = np.clip(self.model.predict(X), 0.0, None)
        critical = np.clip(self.critical_model.predict(X), 0.0, total)

        # 80 % interval from the training residual spread.
        z = 1.2816
        band = z * self.residual_std

        out = pd.DataFrame({
            "target_time": feats["observed_at"],
            "predicted_load_kw": np.round(total, 3),
            "critical_load_kw": np.round(critical, 3),
            "load_kw_p10": np.round(np.clip(total - band, 0.0, None), 3),
            "load_kw_p90": np.round(total + band, 3),
        })
        out["deferrable_load_kw"] = np.round(
            np.clip(total - critical, 0.0, None) * 0.55, 3
        )
        out["input_temperature_c"] = feats["temperature_c"].to_numpy()
        out["input_wind_speed_ms"] = feats["wind_speed_ms"].to_numpy()
        out["model_version"] = MODEL_VERSION
        out["model_name"] = "HistGradientBoostingRegressor"
        out["confidence"] = round(
            float(max(0.0, min(1.0, 1.0 - self.residual_std / max(total.mean(), 1.0)))), 4
        )
        return out

    # -- persistence -------------------------------------------------------

    def save(self, path: str | None = None) -> str:
        target = path or f"{settings.model_dir}/{MODEL_FILE}"
        joblib.dump({
            "model": self.model,
            "critical_model": self.critical_model,
            "feature_names": self.feature_names,
            "residual_std": self.residual_std,
            "report": self.report.as_dict() if self.report else None,
            "version": MODEL_VERSION,
        }, target)
        return target

    @classmethod
    def load(cls, path: str | None = None) -> "LoadForecaster":
        target = path or f"{settings.model_dir}/{MODEL_FILE}"
        blob = joblib.load(target)
        inst = cls()
        inst.model = blob["model"]
        inst.critical_model = blob["critical_model"]
        inst.feature_names = blob["feature_names"]
        inst.residual_std = blob["residual_std"]
        return inst
