"""Explainable AI for POLARIS.

Operators in Antarctica will not act on a number with no justification, so
every POLARIS output has to be able to answer "why".

Three layers
------------
GLOBAL   permutation importance - which features the model relies on overall
LOCAL    per-prediction contributions, computed model-agnostically by
         perturbing each feature to its training median and measuring the
         shift in the prediction (an occlusion / ablation attribution)
CAUSAL   plain-language narrative and counterfactuals derived from the
         physical model, not from the statistics

Local attribution here is exact for the perturbation it describes: it reports
"if this feature had been at its typical value, the prediction would have
moved by X kW". That is directly meaningful to an operator, and unlike a
correlation-based score it cannot mislead about direction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance

from app.config import settings
from app.core.station import THRESHOLDS, WIND
from app.ml.features import FEATURE_DESCRIPTIONS

log = logging.getLogger("polaris.ml.xai")


@dataclass
class FeatureContribution:
    feature: str
    description: str
    value: float
    contribution_kw: float
    direction: str  # increases | decreases | neutral
    rank: int

    def as_dict(self) -> dict:
        return {
            "feature": self.feature,
            "description": self.description,
            "value": round(float(self.value), 4),
            "contribution_kw": round(float(self.contribution_kw), 4),
            "abs_contribution": round(abs(float(self.contribution_kw)), 4),
            "direction": self.direction,
            "rank": self.rank,
        }


@dataclass
class Explanation:
    target: str
    prediction: float
    baseline: float
    timestamp: datetime | None
    contributions: list[FeatureContribution] = field(default_factory=list)
    narrative: str = ""
    counterfactuals: list[str] = field(default_factory=list)
    method: str = "occlusion-to-median attribution"

    def as_dict(self) -> dict:
        return {
            "target": self.target,
            "prediction": round(float(self.prediction), 4),
            "baseline": round(float(self.baseline), 4),
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "method": self.method,
            "contributions": [c.as_dict() for c in self.contributions],
            "narrative": self.narrative,
            "counterfactuals": self.counterfactuals,
        }


class Explainer:
    """Wraps a fitted sklearn regressor with explanation utilities."""

    def __init__(self, model, feature_names: list[str],
                 background: pd.DataFrame, target_name: str = "load_kw") -> None:
        self.model = model
        self.feature_names = list(feature_names)
        self.background = background[self.feature_names].copy()
        self.medians = self.background.median(numeric_only=True)
        self.target_name = target_name
        self._global_cache: list[dict] | None = None

    # -- global ------------------------------------------------------------

    def global_importance(self, X: pd.DataFrame | None = None,
                          y: np.ndarray | None = None,
                          n_repeats: int = 8) -> list[dict]:
        """Permutation importance. Falls back to variance-based sensitivity
        when no labelled sample is supplied."""
        if self._global_cache is not None:
            return self._global_cache

        if X is not None and y is not None and len(X) > 20:
            res = permutation_importance(
                self.model, X[self.feature_names], y,
                n_repeats=n_repeats, random_state=settings.random_seed,
                scoring="neg_mean_absolute_error",
            )
            rows = [
                {
                    "feature": f,
                    "description": FEATURE_DESCRIPTIONS.get(f, f),
                    "importance": float(res.importances_mean[i]),
                    "std": float(res.importances_std[i]),
                }
                for i, f in enumerate(self.feature_names)
            ]
        else:
            sample = self.background.tail(400)
            base = self.model.predict(sample)
            rows = []
            for f in self.feature_names:
                pert = sample.copy()
                pert[f] = self.medians.get(f, 0.0)
                delta = np.abs(base - self.model.predict(pert)).mean()
                rows.append({
                    "feature": f,
                    "description": FEATURE_DESCRIPTIONS.get(f, f),
                    "importance": float(delta),
                    "std": 0.0,
                })

        total = sum(max(0.0, r["importance"]) for r in rows) or 1.0
        for r in rows:
            r["importance_pct"] = round(max(0.0, r["importance"]) / total * 100.0, 2)
            r["importance"] = round(r["importance"], 5)
        rows.sort(key=lambda r: r["importance"], reverse=True)
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        self._global_cache = rows
        return rows

    # -- local -------------------------------------------------------------

    def explain_row(self, row: pd.Series, top_k: int = 8,
                    timestamp: datetime | None = None) -> Explanation:
        """Attribute one prediction to its inputs."""
        x = row[self.feature_names].to_frame().T.astype(float)
        prediction = float(self.model.predict(x)[0])

        med_frame = self.medians.to_frame().T[self.feature_names].astype(float)
        baseline = float(self.model.predict(med_frame)[0])

        contributions: list[FeatureContribution] = []
        for f in self.feature_names:
            perturbed = x.copy()
            perturbed[f] = self.medians.get(f, 0.0)
            delta = prediction - float(self.model.predict(perturbed)[0])
            contributions.append(FeatureContribution(
                feature=f,
                description=FEATURE_DESCRIPTIONS.get(f, f),
                value=float(row.get(f, 0.0)),
                contribution_kw=delta,
                direction=("increases" if delta > 0.05 else
                           "decreases" if delta < -0.05 else "neutral"),
                rank=0,
            ))

        contributions.sort(key=lambda c: abs(c.contribution_kw), reverse=True)
        for i, c in enumerate(contributions, 1):
            c.rank = i
        top = contributions[:top_k]

        return Explanation(
            target=self.target_name,
            prediction=prediction,
            baseline=baseline,
            timestamp=timestamp,
            contributions=top,
            narrative=self._narrative(prediction, baseline, top, row),
            counterfactuals=self._counterfactuals(top, row),
        )

    # -- narrative ---------------------------------------------------------

    def _narrative(self, prediction: float, baseline: float,
                   top: list[FeatureContribution], row: pd.Series) -> str:
        delta = prediction - baseline
        direction = "above" if delta > 0 else "below"
        parts = [
            f"Predicted {self.target_name.replace('_', ' ')} is "
            f"{prediction:.1f} kW, {abs(delta):.1f} kW {direction} the "
            f"typical {baseline:.1f} kW for this plant."
        ]
        if top:
            drivers = []
            for c in top[:3]:
                verb = "pushing it up" if c.contribution_kw > 0 else "holding it down"
                drivers.append(
                    f"{c.description.lower()} (now {c.value:.1f}) is {verb} by "
                    f"{abs(c.contribution_kw):.1f} kW"
                )
            parts.append("The dominant drivers: " + "; ".join(drivers) + ".")

        temp = row.get("temperature_c")
        wind = row.get("wind_speed_ms")
        if temp is not None and wind is not None:
            if temp < THRESHOLDS.extreme_cold_c:
                parts.append(
                    f"At {temp:.1f} C the station is in extreme cold, so "
                    "space heating dominates the load."
                )
            if wind >= THRESHOLDS.storm_wind_ms:
                parts.append(
                    f"Wind of {wind:.1f} m/s is above the "
                    f"{WIND.cut_out_ms:.0f} m/s turbine cut-out: wind "
                    "generation is unavailable despite the strong wind."
                )
            elif wind >= WIND.rated_ms:
                parts.append(
                    f"Wind of {wind:.1f} m/s is at or above rated speed, so "
                    "the turbines are at full output."
                )
        return " ".join(parts)

    def _counterfactuals(self, top: list[FeatureContribution],
                         row: pd.Series) -> list[str]:
        out = []
        for c in top[:3]:
            if abs(c.contribution_kw) < 0.5:
                continue
            typical = float(self.medians.get(c.feature, 0.0))
            out.append(
                f"If {c.description.lower()} were at its typical value "
                f"({typical:.1f}) instead of {c.value:.1f}, the prediction "
                f"would change by {-c.contribution_kw:+.1f} kW."
            )
        return out


# ---------------------------------------------------------------------------
# Decision-level explanation (not model-level)
# ---------------------------------------------------------------------------


def explain_dispatch(state: dict) -> dict:
    """Explain why the optimiser chose a given dispatch for one hour.

    This is a causal explanation grounded in the physical merit order, which
    is what an operator actually needs to audit the decision.
    """
    reasons: list[str] = []
    renewable = float(state.get("renewable_kw", 0.0))
    load = float(state.get("total_load_kw", 0.0))
    gen = float(state.get("generator_output_kw", 0.0))
    dis = float(state.get("battery_discharge_kw", 0.0))
    chg = float(state.get("battery_charge_kw", 0.0))
    soc = float(state.get("battery_soc_pct", 0.0))
    curt = float(state.get("renewable_curtailed_kw", 0.0))
    shed = float(state.get("shed_load_kw", 0.0))

    net = renewable - load
    if net >= 0:
        reasons.append(
            f"Renewable output ({renewable:.1f} kW) exceeds demand "
            f"({load:.1f} kW) by {net:.1f} kW."
        )
        if chg > 0.1:
            reasons.append(
                f"Surplus is charging the battery at {chg:.1f} kW "
                f"(now {soc:.1f}% state of charge)."
            )
        if curt > 0.1:
            reasons.append(
                f"{curt:.1f} kW had to be curtailed - the pack cannot absorb "
                "it and no deferrable load is scheduled in this hour."
            )
    else:
        reasons.append(
            f"Demand ({load:.1f} kW) exceeds renewable output "
            f"({renewable:.1f} kW); deficit is {-net:.1f} kW."
        )
        if dis > 0.1:
            reasons.append(
                f"Battery is covering {dis:.1f} kW of the deficit, which "
                "avoids starting a generator."
            )
        if gen > 0.1:
            loading = float(state.get("generator_loading_pct", 0.0))
            reasons.append(
                f"Generator dispatched at {gen:.1f} kW "
                f"({loading:.0f}% loading) to cover the remainder; "
                f"{int(state.get('generators_running', 0))} unit(s) online."
            )
            if state.get("wet_stacking"):
                reasons.append(
                    "Loading is below the 30% minimum - this risks wet "
                    "stacking and should be corrected by consolidating load."
                )
        if shed > 0.1:
            reasons.append(
                f"{shed:.1f} kW of low-priority load was shed to protect "
                "life-critical and science-critical circuits."
            )

    return {
        "decision": "dispatch",
        "timestamp": state.get("timestamp"),
        "reasons": reasons,
        "merit_order": ["renewable", "battery", "diesel generator", "load shedding"],
        "provenance": "MODELLED / OPTIMIZED",
    }
