"""In-process model registry.

Holds the trained scikit-learn models, retrains them when the real weather
history has grown enough, and persists them to disk so a restart does not
force a cold retrain.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

import pandas as pd

from app.config import settings
from app.ml.anomaly_detector import AnomalyDetector
from app.ml.explainability import Explainer
from app.ml.features import LOAD_FEATURES, build_features, select_matrix
from app.ml.load_forecaster import LoadForecaster
from app.ml.renewable_forecaster import RenewableForecaster

log = logging.getLogger("polaris.ml.registry")


class ModelRegistry:
    """Thread-safe singleton holding the active models."""

    _lock = threading.RLock()

    def __init__(self) -> None:
        self.load_forecaster: LoadForecaster | None = None
        self.renewable_forecaster: RenewableForecaster | None = None
        self.anomaly_detector: AnomalyDetector | None = None
        self.explainer: Explainer | None = None
        self.trained_at: datetime | None = None
        self.training_rows: int = 0
        self.reports: dict = {}
        self.last_error: str | None = None

    # -- state -------------------------------------------------------------

    @property
    def is_trained(self) -> bool:
        return self.load_forecaster is not None and self.renewable_forecaster is not None

    @property
    def has_explainer(self) -> bool:
        return self.explainer is not None

    def status(self) -> dict:
        return {
            "is_trained": self.is_trained,
            "trained_at": self.trained_at.isoformat() if self.trained_at else None,
            "training_rows": self.training_rows,
            "reports": self.reports,
            "last_error": self.last_error,
            "model_dir": settings.model_dir,
        }

    # -- training ----------------------------------------------------------

    def train(self, weather: pd.DataFrame, force: bool = False) -> dict:
        """Train every model on the real weather history."""
        with self._lock:
            if self.is_trained and not force and len(weather) <= self.training_rows:
                return self.status()
            if len(weather) < 48:
                self.last_error = (
                    f"Only {len(weather)} real observations available; "
                    "need at least 48 to train."
                )
                log.warning(self.last_error)
                return self.status()

            try:
                lf = LoadForecaster()
                load_report = lf.fit(weather)

                rf = RenewableForecaster()
                renew_report = rf.fit(weather)

                # Anomaly detector trains on modelled states over real weather.
                from app.ml.energy_state_estimator import EnergyStateEstimator

                sim = EnergyStateEstimator().run(weather)
                states = sim.to_frame()
                ad = AnomalyDetector()
                anom_report = {}
                if len(states) >= 50:
                    anom_report = ad.fit(states)

                # Explainer over the load model.
                feats = build_features(weather)
                X = select_matrix(feats, LOAD_FEATURES)
                explainer = Explainer(
                    lf.model, list(X.columns), X, target_name="station_load_kw"
                )

                self.load_forecaster = lf
                self.renewable_forecaster = rf
                self.anomaly_detector = ad if anom_report else None
                self.explainer = explainer
                self.trained_at = datetime.now(timezone.utc)
                self.training_rows = len(weather)
                self.reports = {
                    "load_forecaster": load_report.as_dict(),
                    "renewable_forecaster": renew_report.as_dict(),
                    "anomaly_detector": anom_report,
                }
                self.last_error = None
                self._persist()
                log.info("Model registry trained on %d real observations", len(weather))
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("Model training failed")
            return self.status()

    def _persist(self) -> None:
        try:
            os.makedirs(settings.model_dir, exist_ok=True)
            if self.load_forecaster:
                self.load_forecaster.save()
            if self.renewable_forecaster:
                self.renewable_forecaster.save()
            if self.anomaly_detector:
                self.anomaly_detector.save()
        except Exception:
            log.exception("Could not persist models (continuing in memory)")

    def try_restore(self) -> bool:
        """Load previously trained models from disk, if present.

        The Explainer is deliberately NOT rebuilt here: it needs a background
        sample of real feature rows, which lives in the database rather than
        the model file. `ensure_explainer()` builds it on first use.
        """
        import joblib

        with self._lock:
            try:
                lf = LoadForecaster.load()
                rf = RenewableForecaster.load()
                self.load_forecaster = lf
                self.renewable_forecaster = rf
                try:
                    self.anomaly_detector = AnomalyDetector.load()
                except Exception:
                    self.anomaly_detector = None

                # Recover the training metadata so /model/info and the Data &
                # Model page do not report "trained on 0 rows" after a restart.
                reports: dict = {}
                for key, fname in (
                    ("load_forecaster", "load_forecaster.joblib"),
                    ("renewable_forecaster", "renewable_forecaster.joblib"),
                    ("anomaly_detector", "anomaly_detector.joblib"),
                ):
                    try:
                        blob = joblib.load(f"{settings.model_dir}/{fname}")
                        rep = blob.get("report") or {}
                        if rep:
                            reports[key] = rep
                    except Exception:
                        continue
                self.reports = reports
                load_rep = reports.get("load_forecaster") or {}
                self.training_rows = int(load_rep.get("n_samples") or 0)
                trained = load_rep.get("trained_at")
                if trained:
                    try:
                        self.trained_at = datetime.fromisoformat(trained)
                    except ValueError:
                        self.trained_at = datetime.now(timezone.utc)
                else:
                    self.trained_at = datetime.now(timezone.utc)

                log.info("Restored models from %s (%d training rows)",
                         settings.model_dir, self.training_rows)
                return True
            except Exception:
                return False

    def ensure_explainer(self, weather: pd.DataFrame):
        """Build the Explainer on demand from real weather history.

        Needed after a process restart: the fitted estimator is restored from
        disk, but the background feature sample the explainer attributes
        against has to be rebuilt from the database.
        """
        with self._lock:
            if self.explainer is not None:
                return self.explainer
            if self.load_forecaster is None or self.load_forecaster.model is None:
                return None
            if weather is None or weather.empty or len(weather) < 24:
                return None
            try:
                feats = build_features(weather)
                X = select_matrix(feats, LOAD_FEATURES)
                self.explainer = Explainer(
                    self.load_forecaster.model, list(X.columns), X,
                    target_name="station_load_kw",
                )
                log.info("Explainer rebuilt from %d rows of real weather", len(X))
            except Exception:
                log.exception("Could not rebuild the explainer")
                self.explainer = None
            return self.explainer


registry = ModelRegistry()
