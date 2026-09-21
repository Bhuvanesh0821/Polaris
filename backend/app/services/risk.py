"""Operational risk assessment.

Collapses the modelled energy state, the AI forecast and the live weather
into one risk level an operator can act on - plus the ranked factors that
produced it, so the number is never a black box.

Scoring is additive and bounded: each factor contributes a weighted score in
[0, 1], the total is normalised, and the band is read off fixed thresholds.
Every factor carries the measurement that triggered it, which is what the
Explainable AI page renders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.core.station import BATTERY, FUEL, GENERATOR, THRESHOLDS, WIND


class RiskLevel(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    SEVERE = "SEVERE"


#: Upper score bound for each band.
_BANDS = [
    (0.15, RiskLevel.LOW),
    (0.35, RiskLevel.MODERATE),
    (0.55, RiskLevel.ELEVATED),
    (0.75, RiskLevel.HIGH),
    (1.01, RiskLevel.SEVERE),
]

RISK_COLORS = {
    RiskLevel.LOW: "#0f7a4f",
    RiskLevel.MODERATE: "#3f8f3f",
    RiskLevel.ELEVATED: "#9a6200",
    RiskLevel.HIGH: "#b3261e",
    RiskLevel.SEVERE: "#7d1710",
}


@dataclass
class RiskFactor:
    key: str
    label: str
    score: float           # 0..1 contribution before weighting
    weight: float
    value: str             # the measurement, formatted for display
    detail: str            # why it matters

    @property
    def weighted(self) -> float:
        return self.score * self.weight

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "score": round(self.score, 4),
            "weight": round(self.weight, 3),
            "weighted": round(self.weighted, 4),
            "value": self.value,
            "detail": self.detail,
        }


@dataclass
class RiskAssessment:
    level: RiskLevel
    score: float               # 0..1
    score_pct: float
    headline: str
    factors: list[RiskFactor] = field(default_factory=list)
    color: str = "#0f7a4f"

    def as_dict(self) -> dict:
        return {
            "level": self.level.value,
            "score": round(self.score, 4),
            "score_pct": round(self.score_pct, 1),
            "headline": self.headline,
            "color": self.color,
            "factors": [f.as_dict() for f in sorted(
                self.factors, key=lambda x: x.weighted, reverse=True
            )],
            "top_drivers": [
                f.label for f in sorted(
                    self.factors, key=lambda x: x.weighted, reverse=True
                )[:3] if f.weighted > 0.01
            ],
            "provenance": "MODELLED",
        }


def _ramp(value: float, good: float, bad: float) -> float:
    """Linear 0..1 ramp. Handles both increasing and decreasing risk."""
    if good == bad:
        return 0.0
    x = (value - good) / (bad - good)
    return max(0.0, min(1.0, x))


def assess(energy: dict, autonomy_h: float | None, weather: dict,
           forecast: dict | None = None) -> RiskAssessment:
    """Compute the operational risk level.

    `energy`   modelled energy state (soc, fuel, load, generation, shedding)
    `autonomy_h` modelled survival time in hours, or None when unbounded
    `weather`  the live measured conditions
    `forecast` optional aggregate of the AI forecast horizon
    """
    factors: list[RiskFactor] = []

    soc = float(energy.get("battery_soc_pct") or 0.0)
    fuel_l = float(energy.get("fuel_level_l") or 0.0)
    load = float(energy.get("total_load_kw") or 0.0)
    critical = float(energy.get("critical_load_kw") or 0.0)
    renewable = float(energy.get("renewable_kw") or 0.0)
    shed = float(energy.get("shed_load_kw") or 0.0)
    unserved = float(energy.get("unserved_load_kw") or 0.0)
    burn_lph = float(energy.get("fuel_consumed_l") or 0.0)
    gens_running = int(energy.get("generators_running") or 0)

    temp = weather.get("temperature_c")
    wind = weather.get("wind_speed_ms")

    # --- 1. energy autonomy (the dominant factor) -------------------------
    if autonomy_h is None:
        auto_score = 0.0
        auto_val = "unbounded"
    else:
        auto_score = _ramp(autonomy_h,
                           good=THRESHOLDS.autonomy_warning_h * 2,
                           bad=THRESHOLDS.autonomy_critical_h)
        auto_val = f"{autonomy_h:.0f} h ({autonomy_h / 24:.1f} d)"
    factors.append(RiskFactor(
        "autonomy", "Energy autonomy", auto_score, 0.30, auto_val,
        f"Below {THRESHOLDS.autonomy_critical_h:.0f} h the station has no "
        "margin to absorb a storm or a plant failure.",
    ))

    # --- 2. battery state of charge ---------------------------------------
    soc_score = _ramp(soc, good=70.0, bad=BATTERY.soc_min_pct)
    factors.append(RiskFactor(
        "battery", "Battery state of charge", soc_score, 0.20, f"{soc:.1f}%",
        f"The pack is the only instantaneous reserve. Its protected floor is "
        f"{BATTERY.soc_min_pct:.0f}%.",
    ))

    # --- 3. fuel endurance -------------------------------------------------
    usable_fuel = max(0.0, fuel_l - FUEL.critical_level_l)
    if burn_lph > 0.01:
        fuel_days = usable_fuel / (burn_lph * 24.0)
    else:
        fuel_days = 999.0
    fuel_score = _ramp(fuel_days,
                       good=THRESHOLDS.fuel_warning_days * 2,
                       bad=THRESHOLDS.fuel_critical_days)
    factors.append(RiskFactor(
        "fuel", "Fuel endurance", fuel_score, 0.18,
        f"{usable_fuel:,.0f} L usable ({fuel_days:.0f} d at current burn)",
        "Resupply at Maitri happens once per austral summer, so fuel is "
        "effectively non-renewable within a season.",
    ))

    # --- 4. renewable coverage of critical load ---------------------------
    coverage = (renewable / critical) if critical > 0 else 1.0
    ren_score = _ramp(coverage, good=0.8, bad=0.0)
    factors.append(RiskFactor(
        "renewable", "Renewable coverage", ren_score, 0.12,
        f"{renewable:.1f} kW vs {critical:.1f} kW critical "
        f"({coverage * 100:.0f}%)",
        "Every kW renewables do not cover must come from stored energy or "
        "diesel.",
    ))

    # --- 5. load shedding / unserved load ---------------------------------
    if unserved > 0.5:
        shed_score = 1.0
        shed_val = f"{unserved:.1f} kW UNSERVED"
        shed_detail = "Demand cannot be met even after shedding."
    elif shed > 0.1:
        shed_score = min(1.0, 0.4 + shed / max(load, 1.0))
        shed_val = f"{shed:.1f} kW shed"
        shed_detail = "Low-priority circuits are already being shed."
    else:
        shed_score = 0.0
        shed_val = "none"
        shed_detail = "All load is being served."
    factors.append(RiskFactor(
        "shedding", "Load shedding", shed_score, 0.10, shed_val, shed_detail,
    ))

    # --- 6. weather severity ----------------------------------------------
    w_score = 0.0
    w_bits = []
    if wind is not None:
        if wind >= WIND.cut_out_ms:
            w_score = max(w_score, 1.0)
            w_bits.append(f"wind {wind:.1f} m/s above turbine cut-out")
        elif wind >= THRESHOLDS.blizzard_wind_ms:
            w_score = max(w_score, 0.55)
            w_bits.append(f"wind {wind:.1f} m/s (blizzard threshold)")
    if temp is not None and temp <= THRESHOLDS.extreme_cold_c:
        w_score = max(w_score, 0.6)
        w_bits.append(f"temperature {temp:.1f} C")
    factors.append(RiskFactor(
        "weather", "Weather severity", w_score, 0.06,
        "; ".join(w_bits) if w_bits else "within normal limits",
        "Storms remove wind generation exactly when heating demand peaks.",
    ))

    # --- 7. dispatchable redundancy ---------------------------------------
    spare = GENERATOR.n_units - gens_running
    red_score = _ramp(float(spare), good=2.0, bad=0.0)
    factors.append(RiskFactor(
        "redundancy", "Generator redundancy", red_score, 0.04,
        f"{gens_running}/{GENERATOR.n_units} running, {spare} spare",
        "With no spare unit a single failure becomes a supply interruption.",
    ))

    # --- 8. forecast deterioration ----------------------------------------
    if forecast:
        ren_fc = float(forecast.get("mean_renewable_kw") or 0.0)
        load_fc = float(forecast.get("mean_load_kw") or 0.0)
        deficit_ratio = 1.0 - (ren_fc / load_fc if load_fc > 0 else 1.0)
        fc_score = _ramp(deficit_ratio, good=0.4, bad=1.0)
        factors.append(RiskFactor(
            "forecast", "Forecast supply deficit", fc_score, 0.10,
            f"{ren_fc:.1f} kW renewable vs {load_fc:.1f} kW demand forecast",
            "The AI forecast is the only forward-looking input; a widening "
            "deficit means the reserve will be drawn down.",
        ))

    total_weight = sum(f.weight for f in factors) or 1.0
    score = sum(f.weighted for f in factors) / total_weight
    score = max(0.0, min(1.0, score))

    level = RiskLevel.SEVERE
    for bound, band in _BANDS:
        if score < bound:
            level = band
            break

    # Unserved critical load is an override, not a weighted contribution:
    # no combination of otherwise-healthy factors should average it away.
    if unserved > 0.5:
        level = RiskLevel.SEVERE

    top = sorted(factors, key=lambda f: f.weighted, reverse=True)
    driver = top[0].label.lower() if top and top[0].weighted > 0.01 else "nominal margins"
    headline = {
        RiskLevel.LOW: f"All reserves healthy; {driver} is the closest watch item.",
        RiskLevel.MODERATE: f"Operating normally, with {driver} worth monitoring.",
        RiskLevel.ELEVATED: f"Margins narrowing - {driver} is the main pressure.",
        RiskLevel.HIGH: f"Reserves under real pressure, driven by {driver}.",
        RiskLevel.SEVERE: f"Critical supply at risk - {driver} requires immediate action.",
    }[level]

    return RiskAssessment(
        level=level, score=score, score_pct=score * 100.0,
        headline=headline, factors=factors, color=RISK_COLORS[level],
    )
