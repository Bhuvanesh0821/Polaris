"""AI decision & recommendation engine.

Turns the modelled state, the AI forecast and the optimiser result into
ranked, explainable operator actions. Every recommendation carries its
drivers, its expected benefit and a counterfactual, because an unexplained
instruction is not actionable at an isolated polar station.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.station import (
    BATTERY,
    FUEL,
    GENERATOR,
    THRESHOLDS,
    WIND,
)
from app.models.base import AlertSeverity, RecommendationCategory, Urgency

log = logging.getLogger("polaris.recommend")


@dataclass
class Rec:
    category: RecommendationCategory
    urgency: Urgency
    title: str
    action: str
    rationale: str
    #: "WHY THIS ACTION?" - short, independently-checkable statements, each
    #: derived from a live model value rather than canned per-category text.
    reasons: list[str] = field(default_factory=list)
    drivers: list[dict] = field(default_factory=list)
    expected_benefit: str | None = None
    estimated_fuel_saving_l: float | None = None
    estimated_autonomy_gain_h: float | None = None
    confidence: float = 0.8
    counterfactual: str | None = None
    evidence: dict = field(default_factory=dict)
    source_module: str = "recommendation_engine"


@dataclass
class AlertSpec:
    code: str
    severity: AlertSeverity
    title: str
    message: str
    subsystem: str
    metric_name: str | None = None
    metric_value: float | None = None
    threshold_value: float | None = None
    recommended_action: str | None = None
    detail: dict = field(default_factory=dict)


URGENCY_RANK = {
    Urgency.IMMEDIATE: 0, Urgency.URGENT: 1,
    Urgency.ELEVATED: 2, Urgency.ROUTINE: 3,
}


def _why_this_action(rec: "Rec", state: dict, autonomy: dict,
                     forecast: dict | None, weather: dict) -> list[str]:
    """Build the "WHY THIS ACTION?" list for one recommendation.

    Each line is a fact read off a current model value, so the explanation
    changes with the plant rather than being fixed text per category. Only
    conditions that are actually true are included - padding the list with
    irrelevant statements would make the real driver harder to spot.
    """
    soc = float(state.get("battery_soc_pct") or 0.0)
    load = float(state.get("total_load_kw") or 0.0)
    critical = float(state.get("critical_load_kw") or 0.0)
    renewable = float(state.get("renewable_kw") or 0.0)
    curtailed = float(state.get("renewable_curtailed_kw") or 0.0)
    gen_kw = float(state.get("generator_output_kw") or 0.0)
    gen_loading = float(state.get("generator_loading_pct") or 0.0)
    fuel_l = float(state.get("fuel_level_l") or 0.0)
    shed = float(state.get("shed_load_kw") or 0.0)
    autonomy_h = autonomy.get("hours")
    wind = weather.get("wind_speed_ms")
    temp = weather.get("temperature_c")

    why: list[str] = []

    # --- forward-looking trends (the spec's motivating example) -----------
    if forecast:
        ren_now = renewable
        ren_next = forecast.get("mean_renewable_kw")
        load_next = forecast.get("mean_load_kw")
        if ren_next is not None and ren_now - ren_next > 1.0:
            why.append(
                f"Renewable generation is forecast to fall from "
                f"{ren_now:.1f} kW to {ren_next:.1f} kW over the horizon."
            )
        elif ren_next is not None and ren_next - ren_now > 1.0:
            why.append(
                f"Renewable generation is forecast to rise to "
                f"{ren_next:.1f} kW, creating a charging window."
            )
        if load_next is not None and load_next - load > 1.0:
            why.append(
                f"Demand is forecast to increase from {load:.1f} kW to "
                f"{load_next:.1f} kW."
            )
        min_ren = forecast.get("min_renewable_kw")
        if min_ren is not None and min_ren < critical * 0.25 and critical > 0:
            why.append(
                f"Renewable output drops to {min_ren:.1f} kW at its worst "
                f"point, against {critical:.1f} kW of critical demand."
            )

    # --- present state ----------------------------------------------------
    if soc <= BATTERY.soc_reserve_pct:
        why.append(
            f"Battery is at {soc:.1f}%, at or below the "
            f"{BATTERY.soc_reserve_pct:.0f}% reserve band."
        )
    elif soc >= 85.0:
        why.append(f"Battery is near full at {soc:.1f}%, so surplus has nowhere to go.")

    if autonomy_h is not None:
        if autonomy_h < THRESHOLDS.autonomy_critical_h:
            why.append(
                f"Energy autonomy is {autonomy_h:.0f} h, under the "
                f"{THRESHOLDS.autonomy_critical_h:.0f} h emergency threshold."
            )
        elif autonomy_h < THRESHOLDS.autonomy_warning_h:
            why.append(
                f"Energy autonomy is {autonomy_h:.0f} h, under the "
                f"{THRESHOLDS.autonomy_warning_h:.0f} h planning reserve."
            )

    if curtailed > 1.0:
        why.append(f"{curtailed:.1f} kW of renewable output is being dumped.")

    if gen_kw > 0.1 and gen_loading < GENERATOR.min_loading_frac * 100:
        why.append(
            f"Generator loading is {gen_loading:.0f}%, below the "
            f"{GENERATOR.min_loading_frac * 100:.0f}% wet-stacking floor."
        )

    if shed > 0.1:
        why.append(f"{shed:.1f} kW of load is already being shed.")

    if wind is not None and wind >= WIND.cut_out_ms:
        why.append(
            f"Wind of {wind:.1f} m/s exceeds the {WIND.cut_out_ms:.0f} m/s "
            "cut-out, so the turbines are feathered."
        )
    if temp is not None and temp <= THRESHOLDS.extreme_cold_c:
        why.append(f"Air temperature is {temp:.1f} C, driving peak heating demand.")

    if fuel_l <= FUEL.low_level_l:
        why.append(
            f"Fuel is down to {fuel_l:,.0f} L, below the "
            f"{FUEL.low_level_l:,.0f} L low-level mark."
        )

    # --- the invariant that justifies acting at all ------------------------
    if rec.urgency in (Urgency.IMMEDIATE, Urgency.URGENT):
        why.append(
            f"{critical:.1f} kW of life- and science-critical load must be "
            "protected without interruption."
        )

    if not why:
        why.append(
            f"All monitored parameters are inside their operating bands "
            f"(battery {soc:.0f}%, autonomy "
            f"{autonomy_h:.0f} h)." if autonomy_h is not None else
            f"All monitored parameters are inside their operating bands "
            f"(battery {soc:.0f}%)."
        )
    return why


def generate(state: dict, autonomy: dict, optimization: dict | None,
             forecast_summary: dict, weather: dict) -> tuple[list[Rec], list[AlertSpec]]:
    """Produce recommendations and alerts from the current modelled picture."""
    recs: list[Rec] = []
    alerts: list[AlertSpec] = []

    soc = float(state.get("battery_soc_pct", 100.0))
    fuel_l = float(state.get("fuel_level_l", 0.0))
    fuel_pct = fuel_l / FUEL.tank_capacity_l * 100.0
    load = float(state.get("total_load_kw", 0.0))
    critical = float(state.get("critical_load_kw", 0.0))
    renewable = float(state.get("renewable_kw", 0.0))
    gen_kw = float(state.get("generator_output_kw", 0.0))
    gen_loading = float(state.get("generator_loading_pct", 0.0))
    running = int(state.get("generators_running", 0))
    curtailed = float(state.get("renewable_curtailed_kw", 0.0))
    ren_frac = float(state.get("renewable_fraction", 0.0))

    autonomy_h = float(autonomy.get("hours") or 0.0)
    temp = weather.get("temperature_c")
    wind = weather.get("wind_speed_ms")

    fuel_burn_lph = float(state.get("fuel_consumed_l", 0.0))
    days_fuel = (
        (fuel_l - FUEL.critical_level_l) / (fuel_burn_lph * 24.0)
        if fuel_burn_lph > 0.01 else None
    )

    # ------------------------------------------------------------------
    # SAFETY - autonomy
    # ------------------------------------------------------------------
    if autonomy_h and autonomy_h < THRESHOLDS.autonomy_critical_h:
        recs.append(Rec(
            category=RecommendationCategory.SAFETY,
            urgency=Urgency.IMMEDIATE,
            title="Energy autonomy below the 24-hour emergency threshold",
            action=(
                "Declare an energy emergency. Shed all P4 deferrable and P3 "
                "operational circuits immediately and hold P1 life-critical "
                "plus essential P2 science only. Verify both fuel transfer "
                "pumps and start a second genset for redundancy."
            ),
            rationale=(
                f"Modelled autonomy is {autonomy_h:.1f} h against a "
                f"{THRESHOLDS.autonomy_critical_h:.0f} h threshold, with the "
                f"battery at {soc:.1f}% and {fuel_l:,.0f} L usable fuel while "
                f"critical demand runs at {critical:.1f} kW."
            ),
            drivers=[
                {"factor": "Estimated autonomy", "value": f"{autonomy_h:.1f} h",
                 "impact": "critical"},
                {"factor": "Battery state of charge", "value": f"{soc:.1f}%",
                 "impact": "high"},
                {"factor": "Critical load", "value": f"{critical:.1f} kW",
                 "impact": "high"},
            ],
            expected_benefit=(
                "Shedding P3 and P4 typically cuts demand by 25-40%, which "
                "extends autonomy proportionally."
            ),
            estimated_autonomy_gain_h=round(autonomy_h * 0.35, 1),
            confidence=0.92,
            counterfactual=(
                "If P4 load were already off, autonomy would be roughly "
                f"{autonomy_h * 1.25:.0f} h instead of {autonomy_h:.0f} h."
            ),
            evidence={"autonomy": autonomy, "soc_pct": soc, "fuel_l": fuel_l},
        ))
        alerts.append(AlertSpec(
            code="AUTONOMY_CRITICAL", severity=AlertSeverity.EMERGENCY,
            title="Energy autonomy critical",
            message=(
                f"Modelled autonomy {autonomy_h:.1f} h is below the "
                f"{THRESHOLDS.autonomy_critical_h:.0f} h emergency threshold."
            ),
            subsystem="energy", metric_name="autonomy_hours",
            metric_value=autonomy_h, threshold_value=THRESHOLDS.autonomy_critical_h,
            recommended_action="Shed to life-critical load and secure fuel supply.",
        ))
    elif autonomy_h and autonomy_h < THRESHOLDS.autonomy_warning_h:
        recs.append(Rec(
            category=RecommendationCategory.SAFETY,
            urgency=Urgency.ELEVATED,
            title="Energy autonomy below the 72-hour planning reserve",
            action=(
                "Defer non-essential laboratory batches and vehicle charging "
                "to the next renewable surplus window. Confirm the fuel "
                "transfer schedule and review the 7-day weather outlook."
            ),
            rationale=(
                f"Autonomy is {autonomy_h:.1f} h, under the "
                f"{THRESHOLDS.autonomy_warning_h:.0f} h reserve that gives the "
                "station time to react to a storm front."
            ),
            drivers=[
                {"factor": "Estimated autonomy", "value": f"{autonomy_h:.1f} h",
                 "impact": "high"},
                {"factor": "Battery state of charge", "value": f"{soc:.1f}%",
                 "impact": "medium"},
            ],
            expected_benefit="Restores the 72-hour buffer before conditions deteriorate.",
            estimated_autonomy_gain_h=round(autonomy_h * 0.18, 1),
            confidence=0.85,
            evidence={"autonomy": autonomy},
        ))
        alerts.append(AlertSpec(
            code="AUTONOMY_LOW", severity=AlertSeverity.WARNING,
            title="Energy autonomy below planning reserve",
            message=f"Autonomy {autonomy_h:.1f} h is below the 72 h reserve.",
            subsystem="energy", metric_name="autonomy_hours",
            metric_value=autonomy_h, threshold_value=THRESHOLDS.autonomy_warning_h,
            recommended_action="Defer P4 load and re-check the weather outlook.",
        ))

    # ------------------------------------------------------------------
    # STORAGE
    # ------------------------------------------------------------------
    if soc <= THRESHOLDS.soc_critical_pct:
        alerts.append(AlertSpec(
            code="BATTERY_CRITICAL", severity=AlertSeverity.CRITICAL,
            title="Battery at protected floor",
            message=(
                f"State of charge {soc:.1f}% is at the "
                f"{BATTERY.soc_min_pct:.0f}% protected floor. Further "
                "discharge risks permanent cell damage."
            ),
            subsystem="battery", metric_name="battery_soc_pct",
            metric_value=soc, threshold_value=THRESHOLDS.soc_critical_pct,
            recommended_action="Bring a generator online to recharge.",
        ))
        recs.append(Rec(
            category=RecommendationCategory.STORAGE_STRATEGY,
            urgency=Urgency.URGENT,
            title="Recharge the battery bank now",
            action=(
                "Start one genset and load it to 75-85% so it both carries "
                "station load and recharges the pack at high efficiency. Stop "
                "once the pack reaches 60%."
            ),
            rationale=(
                f"At {soc:.1f}% the pack is at its floor and has no capacity "
                "left to absorb a load step or ride through a turbine cut-out."
            ),
            drivers=[
                {"factor": "State of charge", "value": f"{soc:.1f}%", "impact": "critical"},
                {"factor": "Renewable output", "value": f"{renewable:.1f} kW",
                 "impact": "high"},
            ],
            expected_benefit=(
                "Recovering to 60% restores roughly "
                f"{BATTERY.nominal_capacity_kwh * 0.4:.0f} kWh of buffer."
            ),
            confidence=0.9,
            counterfactual=(
                "Leaving the pack at its floor means the next turbine cut-out "
                "goes straight to load shedding."
            ),
            evidence={"soc_pct": soc},
        ))
    elif soc <= THRESHOLDS.soc_warning_pct:
        alerts.append(AlertSpec(
            code="BATTERY_LOW", severity=AlertSeverity.WARNING,
            title="Battery below warning level",
            message=f"State of charge {soc:.1f}% is below "
                    f"{THRESHOLDS.soc_warning_pct:.0f}%.",
            subsystem="battery", metric_name="battery_soc_pct",
            metric_value=soc, threshold_value=THRESHOLDS.soc_warning_pct,
            recommended_action="Plan a recharge window in the next surplus period.",
        ))

    # ------------------------------------------------------------------
    # GENERATION DISPATCH
    # ------------------------------------------------------------------
    if running > 0 and 0 < gen_loading < GENERATOR.min_loading_frac * 100:
        recs.append(Rec(
            category=RecommendationCategory.GENERATION_DISPATCH,
            urgency=Urgency.URGENT,
            title="Generator running below its minimum loading floor",
            action=(
                f"Consolidate onto a single unit, or add deferrable load "
                f"(snow melter, vehicle charging) to lift loading above "
                f"{GENERATOR.min_loading_frac * 100:.0f}%."
            ),
            rationale=(
                f"The genset is at {gen_loading:.0f}% loading. Sustained light "
                "loading causes wet stacking: unburnt fuel glazes the bores "
                "and permanently lowers output. Specific fuel consumption at "
                "this loading is roughly 50% worse than at 80%."
            ),
            drivers=[
                {"factor": "Generator loading", "value": f"{gen_loading:.0f}%",
                 "impact": "high"},
                {"factor": "Units online", "value": str(running), "impact": "medium"},
            ],
            expected_benefit=(
                "Raising loading to 80% cuts specific fuel consumption from "
                "about 0.42 to 0.28 L/kWh."
            ),
            estimated_fuel_saving_l=round(gen_kw * 0.14, 1),
            confidence=0.88,
            counterfactual=(
                "Continuing at this loading wastes roughly "
                f"{gen_kw * 0.14:.1f} L/h and shortens the overhaul interval."
            ),
            evidence={"loading_pct": gen_loading, "output_kw": gen_kw},
        ))
        alerts.append(AlertSpec(
            code="GENSET_WET_STACKING", severity=AlertSeverity.WARNING,
            title="Generator wet-stacking risk",
            message=f"Loading {gen_loading:.0f}% is below the "
                    f"{GENERATOR.min_loading_frac * 100:.0f}% floor.",
            subsystem="generator", metric_name="generator_loading_pct",
            metric_value=gen_loading,
            threshold_value=GENERATOR.min_loading_frac * 100,
            recommended_action="Consolidate units or add deferrable load.",
        ))

    # ------------------------------------------------------------------
    # LOAD MANAGEMENT - absorb curtailment
    # ------------------------------------------------------------------
    if curtailed > 3.0:
        recs.append(Rec(
            category=RecommendationCategory.LOAD_MANAGEMENT,
            urgency=Urgency.ELEVATED,
            title=f"Absorb {curtailed:.0f} kW of curtailed renewable output",
            action=(
                "Start the snow melter and begin vehicle charging now. This "
                "energy is free and is otherwise being dumped."
            ),
            rationale=(
                f"{curtailed:.1f} kW is being curtailed because the battery "
                f"is near full ({soc:.0f}%) and no deferrable load is "
                "scheduled. Water production and vehicle charging are exactly "
                "the loads designed to soak this up."
            ),
            drivers=[
                {"factor": "Curtailed power", "value": f"{curtailed:.1f} kW",
                 "impact": "high"},
                {"factor": "State of charge", "value": f"{soc:.0f}%", "impact": "medium"},
                {"factor": "Renewable output", "value": f"{renewable:.1f} kW",
                 "impact": "high"},
            ],
            expected_benefit=(
                f"Displaces about {curtailed * 0.28:.1f} L/h of future diesel "
                "by producing water now instead of later."
            ),
            estimated_fuel_saving_l=round(curtailed * 0.28, 1),
            confidence=0.86,
            counterfactual=(
                "If this load is not started, the same water still has to be "
                "melted later - most likely on diesel."
            ),
            evidence={"curtailed_kw": curtailed, "soc_pct": soc},
        ))

    # ------------------------------------------------------------------
    # FUEL
    # ------------------------------------------------------------------
    if fuel_l <= FUEL.critical_level_l:
        alerts.append(AlertSpec(
            code="FUEL_CRITICAL", severity=AlertSeverity.EMERGENCY,
            title="Fuel at emergency reserve",
            message=f"Bulk fuel {fuel_l:,.0f} L has reached the "
                    f"{FUEL.critical_level_l:,.0f} L emergency reserve.",
            subsystem="fuel", metric_name="fuel_level_l",
            metric_value=fuel_l, threshold_value=FUEL.critical_level_l,
            recommended_action="Life-critical load only. Escalate resupply.",
        ))
    elif days_fuel is not None and days_fuel < THRESHOLDS.fuel_critical_days:
        recs.append(Rec(
            category=RecommendationCategory.FUEL_CONSERVATION,
            urgency=Urgency.URGENT,
            title=f"Fuel reserve covers only {days_fuel:.0f} days at current burn",
            action=(
                "Cut generator hours: raise the battery discharge floor, move "
                "all deferrable load into renewable surplus windows, and "
                "reduce station setpoint temperature by 2 C."
            ),
            rationale=(
                f"At the current {fuel_burn_lph:.1f} L/h burn, usable fuel "
                f"lasts {days_fuel:.1f} days - inside the "
                f"{THRESHOLDS.fuel_critical_days:.0f}-day critical window, and "
                "resupply at Maitri is once per austral summer."
            ),
            drivers=[
                {"factor": "Fuel remaining", "value": f"{fuel_l:,.0f} L",
                 "impact": "critical"},
                {"factor": "Burn rate", "value": f"{fuel_burn_lph:.1f} L/h",
                 "impact": "high"},
                {"factor": "Renewable fraction", "value": f"{ren_frac * 100:.0f}%",
                 "impact": "medium"},
            ],
            expected_benefit="A 2 C setpoint reduction typically cuts heating load 8-12%.",
            estimated_fuel_saving_l=round(fuel_burn_lph * 24 * 0.1, 1),
            confidence=0.83,
            evidence={"fuel_l": fuel_l, "days_remaining": days_fuel},
        ))
    elif fuel_pct < 30.0:
        alerts.append(AlertSpec(
            code="FUEL_LOW", severity=AlertSeverity.WARNING,
            title="Fuel below 30% of tank capacity",
            message=f"Bulk fuel at {fuel_l:,.0f} L ({fuel_pct:.0f}% of tank).",
            subsystem="fuel", metric_name="fuel_level_l",
            metric_value=fuel_l, threshold_value=FUEL.tank_capacity_l * 0.3,
            recommended_action="Review consumption against the resupply window.",
        ))

    # ------------------------------------------------------------------
    # WEATHER-DRIVEN
    # ------------------------------------------------------------------
    if wind is not None and wind >= THRESHOLDS.storm_wind_ms:
        alerts.append(AlertSpec(
            code="TURBINE_CUTOUT", severity=AlertSeverity.CRITICAL,
            title="Wind above turbine cut-out",
            message=(
                f"Measured wind {wind:.1f} m/s exceeds the "
                f"{WIND.cut_out_ms:.0f} m/s cut-out. Wind generation is zero."
            ),
            subsystem="wind", metric_name="wind_speed_ms",
            metric_value=wind, threshold_value=WIND.cut_out_ms,
            recommended_action="Expect full diesel dependency until the wind eases.",
        ))
        recs.append(Rec(
            category=RecommendationCategory.GENERATION_DISPATCH,
            urgency=Urgency.URGENT,
            title="Storm cut-out: plan for zero wind generation",
            action=(
                "Bring a second genset to standby and pre-charge the battery "
                "while the storm passes. Suspend all P4 load."
            ),
            rationale=(
                f"At {wind:.1f} m/s the turbines have feathered for survival, "
                "so the station's largest renewable source is offline exactly "
                "when wind chill is driving heating demand to its peak."
            ),
            drivers=[
                {"factor": "Wind speed", "value": f"{wind:.1f} m/s", "impact": "critical"},
                {"factor": "Turbine cut-out", "value": f"{WIND.cut_out_ms:.0f} m/s",
                 "impact": "high"},
                {"factor": "Temperature", "value": f"{temp:.1f} C" if temp is not None else "n/a",
                 "impact": "high"},
            ],
            expected_benefit="Avoids an unplanned deep discharge during the storm.",
            confidence=0.9,
            evidence={"wind_speed_ms": wind, "cut_out_ms": WIND.cut_out_ms},
        ))
    elif wind is not None and WIND.rated_ms <= wind < THRESHOLDS.storm_wind_ms and soc < 85:
        recs.append(Rec(
            category=RecommendationCategory.STORAGE_STRATEGY,
            urgency=Urgency.ROUTINE,
            title="Strong wind window - charge the battery now",
            action="Raise the charge setpoint and run deferrable load while wind holds.",
            rationale=(
                f"Wind is {wind:.1f} m/s, at or above the "
                f"{WIND.rated_ms:.0f} m/s rated speed, so the array is at full "
                f"output while the pack sits at {soc:.0f}%."
            ),
            drivers=[
                {"factor": "Wind speed", "value": f"{wind:.1f} m/s", "impact": "high"},
                {"factor": "State of charge", "value": f"{soc:.0f}%", "impact": "medium"},
            ],
            expected_benefit="Stores cheap energy ahead of the next lull.",
            estimated_fuel_saving_l=round(renewable * 0.12, 1),
            confidence=0.8,
            evidence={"wind_speed_ms": wind, "soc_pct": soc},
        ))

    if temp is not None and temp <= THRESHOLDS.extreme_cold_c:
        alerts.append(AlertSpec(
            code="EXTREME_COLD", severity=AlertSeverity.WARNING,
            title="Extreme cold",
            message=(
                f"Air temperature {temp:.1f} C is at or below "
                f"{THRESHOLDS.extreme_cold_c:.0f} C. Heating load and genset "
                "starting fuel both rise."
            ),
            subsystem="weather", metric_name="temperature_c",
            metric_value=temp, threshold_value=THRESHOLDS.extreme_cold_c,
            recommended_action="Verify fuel line trace heating and genset pre-heat.",
        ))

    # ------------------------------------------------------------------
    # OPTIMISER-DERIVED
    # ------------------------------------------------------------------
    if optimization:
        saved = float(optimization.get("fuel_saved_l", 0.0))
        if saved > 1.0:
            recs.append(Rec(
                category=RecommendationCategory.LOAD_MANAGEMENT,
                urgency=Urgency.ELEVATED,
                title=f"Adopt the optimised dispatch to save {saved:.0f} L of fuel",
                action=(
                    "Apply the optimiser's deferrable-load schedule: shift "
                    "snow melting and vehicle charging into the renewable "
                    "surplus hours it identified."
                ),
                rationale=(
                    f"Over the {optimization.get('horizon_h', 48)} h horizon the "
                    f"optimised plan burns {optimization.get('optimized_fuel_l', 0):.0f} L "
                    f"against a {optimization.get('baseline_fuel_l', 0):.0f} L "
                    f"baseline - {optimization.get('fuel_saved_pct', 0):.1f}% less, "
                    f"avoiding {optimization.get('co2_avoided_kg', 0):.0f} kg CO2."
                ),
                drivers=[
                    {"factor": "Fuel saving", "value": f"{saved:.0f} L", "impact": "high"},
                    {"factor": "Load deferred",
                     "value": f"{optimization.get('load_deferred_kwh', 0):.0f} kWh",
                     "impact": "medium"},
                    {"factor": "Renewable fraction",
                     "value": f"{optimization.get('renewable_fraction', 0) * 100:.0f}%",
                     "impact": "medium"},
                ],
                expected_benefit=f"{saved:.0f} L of fuel and "
                                 f"{optimization.get('co2_avoided_kg', 0):.0f} kg CO2.",
                estimated_fuel_saving_l=round(saved, 1),
                confidence=0.87,
                counterfactual=(
                    "Running the unoptimised baseline burns "
                    f"{saved:.0f} L more over the same horizon."
                ),
                evidence={"optimization": {
                    k: optimization.get(k) for k in
                    ("baseline_fuel_l", "optimized_fuel_l", "fuel_saved_pct",
                     "renewable_fraction", "load_deferred_kwh")
                }},
                source_module="energy_optimizer",
            ))

    # ------------------------------------------------------------------
    # LOW RENEWABLE GENERATION
    # ------------------------------------------------------------------
    if critical > 0 and renewable < critical * 0.15:
        alerts.append(AlertSpec(
            code="LOW_RENEWABLE_GENERATION",
            severity=AlertSeverity.WARNING if renewable > 0 else AlertSeverity.CRITICAL,
            title="Renewable generation critically low",
            message=(
                f"Renewables are producing {renewable:.1f} kW against "
                f"{critical:.1f} kW of critical demand "
                f"({renewable / critical * 100:.0f}% coverage). Storage and "
                "diesel are carrying the station."
            ),
            subsystem="renewable", metric_name="renewable_kw",
            metric_value=renewable, threshold_value=round(critical * 0.15, 2),
            recommended_action="Protect the battery reserve and plan generator hours.",
        ))

    # ------------------------------------------------------------------
    # HIGH DEMAND
    # ------------------------------------------------------------------
    _demand_ceiling = 165.0  # MODELLED nominal station peak, kW
    if load > _demand_ceiling:
        alerts.append(AlertSpec(
            code="HIGH_DEMAND", severity=AlertSeverity.WARNING,
            title="Station demand above nominal peak",
            message=(
                f"Total load {load:.1f} kW exceeds the {_demand_ceiling:.0f} kW "
                "modelled station peak."
            ),
            subsystem="load", metric_name="total_load_kw",
            metric_value=load, threshold_value=_demand_ceiling,
            recommended_action="Defer P4 load and review concurrent operations.",
        ))

    # ------------------------------------------------------------------
    # GENERATOR FAILURE / NO DISPATCHABLE RESERVE
    # ------------------------------------------------------------------
    if running == 0 and renewable < critical and soc <= BATTERY.soc_reserve_pct:
        alerts.append(AlertSpec(
            code="GENERATOR_UNAVAILABLE", severity=AlertSeverity.CRITICAL,
            title="No dispatchable generation online",
            message=(
                "No generator is running while renewables fall short of "
                f"critical demand ({renewable:.1f} kW vs {critical:.1f} kW) "
                f"and the battery sits at {soc:.1f}%."
            ),
            subsystem="generator", metric_name="generators_running",
            metric_value=float(running), threshold_value=1.0,
            recommended_action="Start a generator immediately.",
        ))

    # ------------------------------------------------------------------
    # CRITICAL LOAD RISK
    # ------------------------------------------------------------------
    available_now = renewable + gen_kw
    if critical > 0 and available_now < critical and soc <= THRESHOLDS.soc_warning_pct:
        alerts.append(AlertSpec(
            code="CRITICAL_LOAD_RISK", severity=AlertSeverity.CRITICAL,
            title="Critical load supply at risk",
            message=(
                f"Live generation ({available_now:.1f} kW) is below critical "
                f"demand ({critical:.1f} kW) with only {soc:.1f}% battery "
                "remaining to bridge the gap."
            ),
            subsystem="energy", metric_name="critical_load_kw",
            metric_value=critical, threshold_value=available_now,
            recommended_action="Shed P3/P4 circuits and start a generator.",
        ))

    # ------------------------------------------------------------------
    # Nothing specific fired, but conditions are not actually clean.
    # Claiming "all normal" while an alert is active would contradict the
    # rest of the dashboard, so issue a monitoring action instead.
    # ------------------------------------------------------------------
    if not recs and alerts:
        worst = min(
            alerts,
            key=lambda a: [AlertSeverity.EMERGENCY, AlertSeverity.CRITICAL,
                           AlertSeverity.WARNING, AlertSeverity.INFO].index(a.severity),
        )
        recs.append(Rec(
            category=RecommendationCategory.SAFETY,
            urgency=Urgency.ELEVATED,
            title=f"Monitor: {worst.title.lower()}",
            action=(
                worst.recommended_action
                or "Monitor the flagged parameter and re-assess at the next refresh."
            ),
            rationale=(
                f"{len(alerts)} alert(s) are active, the most severe being "
                f"{worst.code} ({worst.severity.value}). {worst.message} No "
                "single corrective action is triggered yet, but the condition "
                "should not be left unattended."
            ),
            drivers=[{
                "factor": worst.metric_name or worst.subsystem or "alert",
                "value": (f"{worst.metric_value:.1f}"
                          if worst.metric_value is not None else worst.severity.value),
                "impact": "elevated",
            }],
            expected_benefit="Catches a developing problem before it forces load shedding.",
            confidence=0.8,
            evidence={"active_alerts": [a.code for a in alerts]},
        ))

    # ------------------------------------------------------------------
    # Steady-state positive confirmation - only when genuinely clean
    # ------------------------------------------------------------------
    if not recs:
        recs.append(Rec(
            category=RecommendationCategory.GENERATION_DISPATCH,
            urgency=Urgency.ROUTINE,
            title="Plant operating within all normal bands",
            action="No operator action required. Continue the current dispatch.",
            rationale=(
                f"Battery {soc:.0f}%, fuel {fuel_l:,.0f} L, load {load:.1f} kW "
                f"with {ren_frac * 100:.0f}% renewable share and autonomy of "
                f"{autonomy_h:.0f} h. Every monitored parameter is inside its "
                "operating band."
            ),
            drivers=[
                {"factor": "State of charge", "value": f"{soc:.0f}%", "impact": "nominal"},
                {"factor": "Autonomy", "value": f"{autonomy_h:.0f} h", "impact": "nominal"},
                {"factor": "Renewable fraction", "value": f"{ren_frac * 100:.0f}%",
                 "impact": "nominal"},
            ],
            expected_benefit="Maintains reserve margins.",
            confidence=0.95,
            evidence={"soc_pct": soc, "fuel_l": fuel_l, "autonomy_h": autonomy_h},
        ))

    # ------------------------------------------------------------------
    # Attach the dynamic "WHY THIS ACTION?" explanation to every
    # recommendation, derived from the live model values above.
    # ------------------------------------------------------------------
    for r in recs:
        if not r.reasons:
            r.reasons = _why_this_action(r, state, autonomy, forecast_summary, weather)

    recs.sort(key=lambda r: URGENCY_RANK.get(r.urgency, 9))
    return recs, alerts
