"""Anomaly detection over the modelled energy state and real weather.

Two complementary detectors:

* IsolationForest on the multivariate operating point - catches combinations
  that are individually plausible but jointly unusual (e.g. high load with
  high renewable output and a falling state of charge).
* Deterministic rule checks - catch the specific failure signatures polar
  power plants actually exhibit, where an unsupervised score alone would be
  too vague to act on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from app.config import settings
from app.core.station import BATTERY, GENERATOR, THRESHOLDS

log = logging.getLogger("polaris.ml.anomaly")

MODEL_VERSION = "anomaly-iforest-1.0.0"
MODEL_FILE = "anomaly_detector.joblib"

DETECTOR_FEATURES = [
    "total_load_kw", "renewable_kw", "generator_output_kw",
    "battery_soc_pct", "temperature_c", "wind_speed_ms",
    "renewable_fraction", "generator_loading_pct",
]


@dataclass
class Anomaly:
    timestamp: datetime
    code: str
    severity: str
    message: str
    metric: str | None = None
    value: float | None = None
    threshold: float | None = None
    score: float | None = None
    detector: str = "rule"

    def as_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
            "score": self.score,
            "detector": self.detector,
        }


class AnomalyDetector:
    def __init__(self, contamination: float = 0.03) -> None:
        self.contamination = contamination
        self.model: IsolationForest | None = None
        self.scaler: StandardScaler | None = None
        self.feature_names = list(DETECTOR_FEATURES)
        self.threshold_score: float = 0.0

    # -- training ----------------------------------------------------------

    def fit(self, states: pd.DataFrame) -> dict:
        X = self._matrix(states)
        if len(X) < 50:
            raise ValueError("Need at least 50 rows to fit the anomaly detector")
        self.scaler = StandardScaler().fit(X)
        Xs = self.scaler.transform(X)
        self.model = IsolationForest(
            n_estimators=200, contamination=self.contamination,
            random_state=settings.random_seed, n_jobs=-1,
        ).fit(Xs)
        scores = self.model.score_samples(Xs)
        self.threshold_score = float(np.percentile(scores, self.contamination * 100))
        return {
            "model_version": MODEL_VERSION,
            "model_type": "IsolationForest",
            "n_samples": int(len(X)),
            "contamination": self.contamination,
            "threshold_score": round(self.threshold_score, 5),
            "feature_names": self.feature_names,
        }

    def _matrix(self, df: pd.DataFrame) -> pd.DataFrame:
        X = pd.DataFrame(index=df.index)
        for name in self.feature_names:
            X[name] = pd.to_numeric(df[name], errors="coerce") if name in df else 0.0
        return X.fillna(X.median(numeric_only=True)).fillna(0.0)

    # -- inference ---------------------------------------------------------

    def score(self, states: pd.DataFrame) -> np.ndarray:
        """Lower score = more anomalous. Returns zeros if not yet trained."""
        if self.model is None or self.scaler is None:
            return np.zeros(len(states))
        X = self._matrix(states)
        return self.model.score_samples(self.scaler.transform(X))

    def detect(self, states: pd.DataFrame) -> list[Anomaly]:
        """Run both detectors over a frame of modelled energy states."""
        if states.empty:
            return []
        found: list[Anomaly] = []
        scores = self.score(states)

        for i, row in enumerate(states.to_dict("records")):
            ts = row.get("timestamp") or row.get("recorded_at")
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            elif hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            if ts is None:
                continue

            s = float(scores[i]) if len(scores) > i else 0.0
            if self.model is not None and s < self.threshold_score:
                found.append(Anomaly(
                    timestamp=ts, code="MULTIVARIATE_OUTLIER", severity="WARNING",
                    message=(
                        "Operating point is statistically unusual against the "
                        "learned normal envelope for this plant."
                    ),
                    score=round(s, 5), detector="isolation_forest",
                ))

            found.extend(self._rules(ts, row, s))
        return found

    @staticmethod
    def _rules(ts: datetime, row: dict, score: float) -> list[Anomaly]:
        out: list[Anomaly] = []
        soc = float(row.get("battery_soc_pct", 100.0))
        load = float(row.get("total_load_kw", 0.0))
        gen_load = float(row.get("generator_loading_pct", 0.0))
        running = int(row.get("generators_running", 0))
        unserved = float(row.get("unserved_load_kw", 0.0))
        curtailed = float(row.get("renewable_curtailed_kw", 0.0))
        wind = float(row.get("wind_speed_ms", 0.0))

        if soc <= THRESHOLDS.soc_critical_pct:
            out.append(Anomaly(
                ts, "BATTERY_SOC_CRITICAL", "CRITICAL",
                f"Battery state of charge {soc:.1f}% is at or below the "
                f"{THRESHOLDS.soc_critical_pct:.0f}% critical floor.",
                "battery_soc_pct", soc, THRESHOLDS.soc_critical_pct, score,
            ))
        elif soc <= THRESHOLDS.soc_warning_pct:
            out.append(Anomaly(
                ts, "BATTERY_SOC_LOW", "WARNING",
                f"Battery state of charge {soc:.1f}% is below the "
                f"{THRESHOLDS.soc_warning_pct:.0f}% warning level.",
                "battery_soc_pct", soc, THRESHOLDS.soc_warning_pct, score,
            ))

        if running > 0 and 0 < gen_load < GENERATOR.min_loading_frac * 100:
            out.append(Anomaly(
                ts, "GENSET_WET_STACKING", "WARNING",
                f"Generator loading {gen_load:.0f}% is below the "
                f"{GENERATOR.min_loading_frac * 100:.0f}% minimum. Prolonged "
                "light loading causes wet stacking and cylinder glazing.",
                "generator_loading_pct", gen_load,
                GENERATOR.min_loading_frac * 100, score,
            ))

        if unserved > 0.5:
            out.append(Anomaly(
                ts, "UNSERVED_LOAD", "EMERGENCY",
                f"{unserved:.1f} kW of demand could not be served by any "
                "source after shedding.",
                "unserved_load_kw", unserved, 0.0, score,
            ))

        if curtailed > 5.0:
            out.append(Anomaly(
                ts, "RENEWABLE_CURTAILMENT", "INFO",
                f"{curtailed:.1f} kW of renewable output curtailed - storage "
                "full and no deferrable load available to absorb it.",
                "renewable_curtailed_kw", curtailed, 5.0, score,
            ))

        if wind >= THRESHOLDS.storm_wind_ms:
            out.append(Anomaly(
                ts, "TURBINE_STORM_CUTOUT", "WARNING",
                f"Measured wind {wind:.1f} m/s is at or above the "
                f"{THRESHOLDS.storm_wind_ms:.0f} m/s turbine cut-out. Wind "
                "generation drops to zero exactly when heating demand peaks.",
                "wind_speed_ms", wind, THRESHOLDS.storm_wind_ms, score,
            ))

        if load > 0 and running == 0 and soc < BATTERY.soc_reserve_pct:
            out.append(Anomaly(
                ts, "NO_DISPATCHABLE_RESERVE", "CRITICAL",
                "No generator online while the battery sits below its reserve "
                "band - the plant has no dispatchable headroom.",
                "battery_soc_pct", soc, BATTERY.soc_reserve_pct, score,
            ))
        return out

    # -- persistence -------------------------------------------------------

    def save(self, path: str | None = None) -> str:
        target = path or f"{settings.model_dir}/{MODEL_FILE}"
        joblib.dump({
            "model": self.model, "scaler": self.scaler,
            "feature_names": self.feature_names,
            "threshold_score": self.threshold_score,
            "contamination": self.contamination, "version": MODEL_VERSION,
        }, target)
        return target

    @classmethod
    def load(cls, path: str | None = None) -> "AnomalyDetector":
        target = path or f"{settings.model_dir}/{MODEL_FILE}"
        blob = joblib.load(target)
        inst = cls(contamination=blob.get("contamination", 0.03))
        inst.model = blob["model"]
        inst.scaler = blob["scaler"]
        inst.feature_names = blob["feature_names"]
        inst.threshold_score = blob["threshold_score"]
        return inst
