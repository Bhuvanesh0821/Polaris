"""Polar crisis simulator.

Runs what-if scenarios against the modelled energy system. Output is ALWAYS
labelled SIMULATED SCENARIO - NOT LIVE STATION TELEMETRY, and simulations are
never written into the live energy-state tables.

A scenario perturbs the *weather inputs* and/or *asset availability*, then
re-runs the whole chain (generation -> state estimation -> survival) so the
result reflects the same physics as normal operation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.core.station import (
    BATTERY,
    FUEL,
    GENERATOR,
    SIMULATION_DISCLAIMER,
    THRESHOLDS,
)
from app.ml.energy_state_estimator import EnergyStateEstimator
from app.models.base import ScenarioType
from app.services import risk as risk_engine
from app.services.energy_model import estimate_autonomy

log = logging.getLogger("polaris.crisis")


@dataclass
class ScenarioDefinition:
    scenario: ScenarioType
    label: str
    description: str
    #: multiplicative / absolute perturbations applied to the weather frame
    wind_speed_factor: float = 1.0
    wind_speed_add: float = 0.0
    #: Sustained-storm floor. A Category-1 Antarctic storm holds winds above
    #: the turbine cut-out for its duration; without a floor, hours that
    #: started calm would be scaled up into the turbine's *productive* band,
    #: which contradicts the scenario's whole premise.
    wind_speed_floor_ms: float | None = None
    solar_factor: float = 1.0
    temperature_offset_c: float = 0.0
    demand_multiplier: float = 1.0
    turbines_available: int | None = None
    generators_available: int | None = None
    solar_available: bool = True
    operator_note: str = ""


SCENARIOS: dict[ScenarioType, ScenarioDefinition] = {
    ScenarioType.NORMAL_OPERATION: ScenarioDefinition(
        scenario=ScenarioType.NORMAL_OPERATION,
        label="Normal Operation",
        description=(
            "Baseline. The live weather forecast is used unmodified with all "
            "plant available. This is the reference every other scenario is "
            "compared against."
        ),
        operator_note="Reference case for comparison.",
    ),
    ScenarioType.SEVERE_STORM: ScenarioDefinition(
        scenario=ScenarioType.SEVERE_STORM,
        label="Severe Storm / Blizzard",
        description=(
            "A Category-1 Antarctic storm: winds driven above the 25 m/s "
            "turbine cut-out, a 9 C drop in effective temperature and heavy "
            "blowing snow blanking the PV array. The cruel detail is that "
            "wind generation goes to ZERO exactly when heating demand peaks, "
            "because the turbines must feather to survive."
        ),
        wind_speed_factor=1.9,
        wind_speed_add=12.0,
        wind_speed_floor_ms=28.0,  # sustained above the 25 m/s cut-out
        solar_factor=0.08,
        temperature_offset_c=-9.0,
        demand_multiplier=1.18,
        operator_note=(
            "Turbines feather above cut-out. Expect full diesel dependency "
            "for the duration."
        ),
    ),
    ScenarioType.LOW_SOLAR: ScenarioDefinition(
        scenario=ScenarioType.LOW_SOLAR,
        label="Low Solar / Polar Night",
        description=(
            "Extended darkness or heavy persistent overcast: PV output falls "
            "to near zero for the whole horizon while lighting and heating "
            "loads rise. This is the normal winter condition at Maitri, not "
            "an exotic failure."
        ),
        solar_factor=0.02,
        demand_multiplier=1.08,
        temperature_offset_c=-4.0,
        operator_note="Wind and stored energy carry the entire station.",
    ),
    ScenarioType.WIND_FAILURE: ScenarioDefinition(
        scenario=ScenarioType.WIND_FAILURE,
        label="Wind Turbine Failure",
        description=(
            "Mechanical or icing failure takes the turbine array offline. "
            "With the dominant renewable source gone, the plant falls back on "
            "storage and diesel."
        ),
        turbines_available=0,
        operator_note="Check gearbox/rime accretion. Fuel burn rises sharply.",
    ),
    ScenarioType.GENERATOR_FAILURE: ScenarioDefinition(
        scenario=ScenarioType.GENERATOR_FAILURE,
        label="Generator Failure",
        description=(
            "Two of three diesel units are unavailable, leaving a single "
            "genset. Dispatchable capacity collapses to one unit and the "
            "battery must absorb every fluctuation."
        ),
        generators_available=1,
        operator_note="Single point of failure. Protect the remaining unit.",
    ),
    ScenarioType.HIGH_DEMAND: ScenarioDefinition(
        scenario=ScenarioType.HIGH_DEMAND,
        label="High Demand Surge",
        description=(
            "Summer science campaign at full intensity: extra crew, drilling "
            "and laboratory equipment running, all vehicles charging. Demand "
            "rises 45% above the seasonal norm."
        ),
        demand_multiplier=1.45,
        operator_note="Consider scheduling campaigns around renewable surplus.",
    ),
    ScenarioType.COMBINED_CRISIS: ScenarioDefinition(
        scenario=ScenarioType.COMBINED_CRISIS,
        label="Combined Crisis",
        description=(
            "The realistic worst case: a severe storm arrives while one "
            "generator is already down and the turbines have iced up. This is "
            "the compound-failure case that determines whether the station's "
            "reserve sizing is actually adequate."
        ),
        wind_speed_factor=1.9,
        wind_speed_add=12.0,
        wind_speed_floor_ms=28.0,
        solar_factor=0.03,
        temperature_offset_c=-12.0,
        demand_multiplier=1.25,
        turbines_available=0,
        generators_available=2,
        operator_note=(
            "Compound failure. Shed to life-critical only and verify fuel "
            "reserve before the storm front arrives."
        ),
    ),
}


@dataclass
class ScenarioOverrides:
    """Operator slider values. Any field left None keeps the preset value."""

    wind_speed_ms: float | None = None
    solar_radiation_wm2: float | None = None
    temperature_c: float | None = None
    demand_multiplier: float | None = None
    initial_soc_pct: float | None = None
    initial_fuel_l: float | None = None
    generators_available: int | None = None
    turbines_available: int | None = None

    def active(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class StateSnapshot:
    """Before/after comparison point for the simulator UI."""

    label: str
    battery_soc_pct: float
    fuel_level_l: float
    total_load_kw: float
    critical_load_kw: float
    renewable_kw: float
    generator_kw: float
    autonomy_hours: float
    risk_level: str
    shed_kw: float

    def as_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v)
                for k, v in self.__dict__.items()}


@dataclass
class CrisisResult:
    scenario: ScenarioType
    label: str
    description: str
    duration_h: int
    severity: float
    survival_hours: float
    baseline_survival_hours: float
    survival_delta_hours: float
    min_soc_pct: float
    fuel_used_l: float
    fuel_remaining_l: float
    load_shed_kwh: float
    unserved_critical_kwh: float
    critical_load_secured: bool
    blackout_occurred: bool
    time_to_first_shed_h: float | None
    severity_rating: str
    timeline: list[dict] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    summary: str = ""
    parameters: dict = field(default_factory=dict)
    #: Side-by-side comparison points for the simulator UI.
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    #: Scenario-specific operator action with its reasoning.
    recommendation: dict = field(default_factory=dict)
    #: Which operator sliders were actually moved away from the preset.
    overrides_applied: dict = field(default_factory=dict)
    disclaimer: str = SIMULATION_DISCLAIMER

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["scenario"] = self.scenario.value
        return d


class CrisisSimulator:
    """Applies a scenario to a real weather forecast and re-runs the model."""

    def __init__(self, estimator: EnergyStateEstimator | None = None) -> None:
        self.estimator = estimator or EnergyStateEstimator()

    @staticmethod
    def list_scenarios() -> list[dict]:
        return [
            {
                "scenario": d.scenario.value,
                "label": d.label,
                "description": d.description,
                "operator_note": d.operator_note,
                "perturbations": {
                    "wind_speed_factor": d.wind_speed_factor,
                    "wind_speed_add_ms": d.wind_speed_add,
                    "solar_factor": d.solar_factor,
                    "temperature_offset_c": d.temperature_offset_c,
                    "demand_multiplier": d.demand_multiplier,
                    "turbines_available": d.turbines_available,
                    "generators_available": d.generators_available,
                },
                "disclaimer": SIMULATION_DISCLAIMER,
            }
            for d in SCENARIOS.values()
        ]

    def _perturb(self, weather: pd.DataFrame, d: ScenarioDefinition,
                 severity: float,
                 overrides: "ScenarioOverrides | None" = None) -> pd.DataFrame:
        """Apply the scenario to the weather frame. Never mutates the input.

        Operator overrides are applied AFTER the scenario preset, so a slider
        always wins over the preset it was nudged away from.
        """
        out = weather.copy()

        def scale(factor: float) -> float:
            """Blend a perturbation toward 1.0 by the severity slider."""
            return 1.0 + (factor - 1.0) * severity

        if "wind_speed_ms" in out:
            out["wind_speed_ms"] = (
                out["wind_speed_ms"] * scale(d.wind_speed_factor)
                + d.wind_speed_add * severity
            ).clip(lower=0.0)
            if d.wind_speed_floor_ms is not None:
                # Blend the floor in with severity so a half-strength storm is
                # genuinely milder than a full one.
                floor = d.wind_speed_floor_ms * min(1.0, severity)
                out["wind_speed_ms"] = out["wind_speed_ms"].clip(lower=floor)
        if "temperature_c" in out:
            out["temperature_c"] = out["temperature_c"] + d.temperature_offset_c * severity
        for col in ("solar_radiation_wm2", "direct_radiation_wm2",
                    "diffuse_radiation_wm2"):
            if col in out:
                out[col] = out[col] * scale(d.solar_factor)

        # --- explicit operator overrides -----------------------------------
        if overrides is not None:
            if overrides.wind_speed_ms is not None and "wind_speed_ms" in out:
                out["wind_speed_ms"] = float(overrides.wind_speed_ms)
            if overrides.temperature_c is not None and "temperature_c" in out:
                out["temperature_c"] = float(overrides.temperature_c)
            if overrides.solar_radiation_wm2 is not None:
                for col in ("solar_radiation_wm2", "direct_radiation_wm2",
                            "diffuse_radiation_wm2"):
                    if col in out:
                        # Split a forced GHI into a plausible beam/diffuse mix
                        # rather than setting all three to the same number.
                        share = {"solar_radiation_wm2": 1.0,
                                 "direct_radiation_wm2": 0.6,
                                 "diffuse_radiation_wm2": 0.4}[col]
                        out[col] = float(overrides.solar_radiation_wm2) * share

        # Recompute the derived physics the perturbation invalidates.
        if {"temperature_c", "wind_speed_ms"}.issubset(out.columns):
            t = out["temperature_c"]
            v_kmh = out["wind_speed_ms"] * 3.6
            v16 = v_kmh.clip(lower=0.0) ** 0.16
            wc = 13.12 + 0.6215 * t - 11.37 * v16 + 0.3965 * t * v16
            out["wind_chill_c"] = np.where((t <= 10.0) & (v_kmh >= 4.8), wc, t)
            out["air_density_kg_m3"] = (
                out.get("pressure_hpa", pd.Series(985.0, index=out.index)).fillna(985.0)
                * 100.0
            ) / (287.05 * (t + 273.15).clip(lower=150.0))
        return out

    def simulate(self, weather: pd.DataFrame, scenario: ScenarioType,
                 initial_soc_pct: float, initial_fuel_l: float,
                 duration_h: int = 72, severity: float = 1.0,
                 overrides: ScenarioOverrides | None = None) -> CrisisResult:
        """Run one scenario. `weather` must be a real forecast frame."""
        d = SCENARIOS[scenario]
        severity = max(0.1, min(2.0, severity))
        horizon = weather.head(duration_h).copy()
        if horizon.empty:
            raise ValueError("No weather rows available for the simulation horizon")

        ov = overrides or ScenarioOverrides()
        if ov.initial_soc_pct is not None:
            initial_soc_pct = float(ov.initial_soc_pct)
        if ov.initial_fuel_l is not None:
            initial_fuel_l = float(ov.initial_fuel_l)

        turbines = ov.turbines_available if ov.turbines_available is not None \
            else d.turbines_available
        generators = ov.generators_available if ov.generators_available is not None \
            else d.generators_available
        demand_mult = ov.demand_multiplier if ov.demand_multiplier is not None \
            else 1.0 + (d.demand_multiplier - 1.0) * severity

        perturbed = self._perturb(horizon, d, severity, ov)

        # --- baseline (unperturbed, all plant available) ------------------
        base_run = self.estimator.run(
            horizon, initial_soc_pct=initial_soc_pct, initial_fuel_l=initial_fuel_l,
        )
        base_autonomy = self._autonomy_from_run(base_run, initial_fuel_l)

        # --- scenario run --------------------------------------------------
        run = self.estimator.run(
            perturbed,
            initial_soc_pct=initial_soc_pct,
            initial_fuel_l=initial_fuel_l,
            turbines_available=turbines,
            generators_available=generators,
            solar_available=d.solar_available,
            demand_multiplier=demand_mult,
        )
        autonomy = self._autonomy_from_run(
            run, initial_fuel_l, generators_available=generators
        )

        # --- timeline -----------------------------------------------------
        timeline = []
        first_shed: float | None = None
        for i, s in enumerate(run.states):
            if first_shed is None and s.shed_load_kw > 0.1:
                first_shed = float(i)
            timeline.append({
                "hour": i,
                "timestamp": s.timestamp.isoformat(),
                "temperature_c": s.temperature_c,
                "wind_speed_ms": s.wind_speed_ms,
                "load_kw": s.total_load_kw,
                "critical_load_kw": s.critical_load_kw,
                "wind_kw": s.wind_generation_kw,
                "solar_kw": s.solar_generation_kw,
                "renewable_kw": s.renewable_kw,
                "generator_kw": s.generator_output_kw,
                "battery_soc_pct": s.battery_soc_pct,
                "fuel_level_l": s.fuel_level_l,
                "shed_kw": s.shed_load_kw,
                "unserved_kw": s.unserved_load_kw,
            })

        fuel_used = initial_fuel_l - run.final_fuel_l
        secured = run.total_unserved_critical_kwh <= 0.01

        # If critical load went unserved, the station has ALREADY failed -
        # reporting a forward-looking "autonomy" would be perverse, because
        # heavy shedding cuts demand and therefore inflates the figure. Report
        # instead how long it lasted before the first unserved hour.
        if not secured:
            failed_at = next(
                (i for i, s in enumerate(run.states) if s.unserved_load_kw > 0.01),
                0,
            )
            autonomy = float(failed_at)
        rating = self._rate(autonomy, run, secured, base_autonomy)
        actions = self._actions(d, run, autonomy)

        before = self._snapshot("Before (baseline)", base_run, base_autonomy)
        after = self._snapshot(f"After ({d.label})", run, autonomy)
        recommendation = self._recommendation(d, run, autonomy, base_autonomy,
                                              secured, rating)

        summary = (
            f"{d.label}: modelled autonomy {self._fmt_hours(autonomy)} against "
            f"a baseline of {self._fmt_hours(base_autonomy)} "
            f"({autonomy - base_autonomy:+.0f} h). Battery bottoms at "
            f"{run.min_soc_pct:.1f}%, {fuel_used:.0f} L of fuel burned over "
            f"{len(run.states)} h. "
            + ("Critical load stayed secured throughout."
               if secured else
               f"CRITICAL LOAD NOT SECURED: supply failed after "
               f"{autonomy:.0f} h with "
               f"{run.total_unserved_critical_kwh:.1f} kWh unserved.")
        )

        return CrisisResult(
            scenario=scenario, label=d.label, description=d.description,
            duration_h=len(run.states), severity=severity,
            survival_hours=round(autonomy, 2),
            baseline_survival_hours=round(base_autonomy, 2),
            survival_delta_hours=round(autonomy - base_autonomy, 2),
            min_soc_pct=round(run.min_soc_pct, 2),
            fuel_used_l=round(fuel_used, 2),
            fuel_remaining_l=round(run.final_fuel_l, 2),
            load_shed_kwh=round(run.total_shed_kwh, 2),
            unserved_critical_kwh=round(run.total_unserved_critical_kwh, 3),
            critical_load_secured=secured,
            blackout_occurred=run.blackout,
            time_to_first_shed_h=first_shed,
            severity_rating=rating,
            timeline=timeline, actions_taken=actions, summary=summary,
            before=before.as_dict(), after=after.as_dict(),
            recommendation=recommendation,
            overrides_applied=ov.active(),
            parameters={
                "wind_speed_factor": d.wind_speed_factor,
                "wind_speed_add_ms": d.wind_speed_add,
                "solar_factor": d.solar_factor,
                "temperature_offset_c": d.temperature_offset_c,
                "demand_multiplier": d.demand_multiplier,
                "turbines_available": d.turbines_available,
                "generators_available": d.generators_available,
                "severity": severity,
                "initial_soc_pct": initial_soc_pct,
                "initial_fuel_l": initial_fuel_l,
            },
        )

    @staticmethod
    def _snapshot(label: str, run, autonomy_h: float) -> StateSnapshot:
        """Condense a simulation run into one comparison point.

        Uses the WORST hour rather than the last, so the before/after panels
        compare the tightest moment of each run instead of wherever the
        horizon happened to end.
        """
        if not run.states:
            return StateSnapshot(
                label=label, battery_soc_pct=0.0, fuel_level_l=0.0,
                total_load_kw=0.0, critical_load_kw=0.0, renewable_kw=0.0,
                generator_kw=0.0, autonomy_hours=0.0, risk_level="UNKNOWN",
                shed_kw=0.0,
            )
        worst = min(run.states, key=lambda s: s.battery_soc_pct)
        last = run.states[-1]

        assessment = risk_engine.assess(
            energy={
                "battery_soc_pct": worst.battery_soc_pct,
                "fuel_level_l": last.fuel_level_l,
                "total_load_kw": worst.total_load_kw,
                "critical_load_kw": worst.critical_load_kw,
                "renewable_kw": worst.renewable_kw,
                "shed_load_kw": worst.shed_load_kw,
                "unserved_load_kw": worst.unserved_load_kw,
                "fuel_consumed_l": worst.fuel_consumed_l,
                "generators_running": worst.generators_running,
            },
            autonomy_h=autonomy_h,
            weather={"temperature_c": worst.temperature_c,
                     "wind_speed_ms": worst.wind_speed_ms},
        )

        return StateSnapshot(
            label=label,
            battery_soc_pct=worst.battery_soc_pct,
            fuel_level_l=last.fuel_level_l,
            total_load_kw=worst.total_load_kw,
            critical_load_kw=worst.critical_load_kw,
            renewable_kw=worst.renewable_kw,
            generator_kw=worst.generator_output_kw,
            autonomy_hours=autonomy_h,
            risk_level=assessment.level.value,
            shed_kw=worst.shed_load_kw,
        )

    @staticmethod
    def _recommendation(d: ScenarioDefinition, run, autonomy: float,
                        baseline: float, secured: bool, rating: str) -> dict:
        """Operator action for this scenario, with its reasoning.

        Every clause is derived from the simulated numbers, so the advice
        moves with the scenario rather than being canned text per preset.
        """
        reasons: list[str] = []
        min_soc = run.min_soc_pct
        shed = run.total_shed_kwh
        delta = autonomy - baseline

        if not secured:
            action = ("Declare an energy emergency and shed to life-critical "
                      "circuits only. Bring every available generator online.")
            urgency = "IMMEDIATE"
        elif rating == "SEVERE":
            action = ("Pre-emptively shed P4 deferrable and P3 operational "
                      "load, and hold both duty generators available before "
                      "conditions deteriorate further.")
            urgency = "URGENT"
        elif rating == "ELEVATED":
            action = ("Suspend deferrable load (snow melting, vehicle "
                      "charging) and pre-charge the battery while generation "
                      "is still available.")
            urgency = "ELEVATED"
        else:
            action = ("Continue current dispatch. Maintain the deferrable-load "
                      "schedule and keep one generator on standby.")
            urgency = "ROUTINE"

        if delta < -1:
            reasons.append(
                f"Autonomy falls {abs(delta):.0f} h below the baseline case "
                f"({autonomy:.0f} h against {baseline:.0f} h)."
            )
        if min_soc <= BATTERY.soc_reserve_pct:
            reasons.append(
                f"Battery reaches {min_soc:.0f}%, at or below the "
                f"{BATTERY.soc_reserve_pct:.0f}% reserve band."
            )
        if shed > 0.5:
            reasons.append(
                f"{shed:.0f} kWh of low-priority load has to be shed to keep "
                "critical circuits supplied."
            )
        if d.turbines_available == 0:
            reasons.append("Wind generation is unavailable for the whole horizon.")
        elif d.wind_speed_factor > 1.4:
            reasons.append(
                "Wind is driven above the turbine cut-out, so the array "
                "feathers exactly when heating demand peaks."
            )
        if d.solar_factor < 0.2:
            reasons.append("Solar input is effectively zero across the horizon.")
        if d.generators_available is not None and d.generators_available < GENERATOR.n_units:
            reasons.append(
                f"Only {d.generators_available} of {GENERATOR.n_units} "
                "generators are available, so there is no dispatchable spare."
            )
        if d.demand_multiplier > 1.1:
            reasons.append(
                f"Demand runs {(d.demand_multiplier - 1) * 100:.0f}% above the "
                "seasonal norm."
            )
        if run.final_fuel_l < FUEL.low_level_l:
            reasons.append(
                f"Fuel ends the horizon at {run.final_fuel_l:,.0f} L, below the "
                f"{FUEL.low_level_l:,.0f} L low-level mark."
            )
        if not reasons:
            reasons.append(
                "All reserves stay within their operating bands for the whole "
                "horizon."
            )

        return {
            "action": action,
            "urgency": urgency,
            "reasons": reasons,
            "expected_benefit": (
                "Protects life-critical supply and preserves the fuel reserve "
                "for the remainder of the season."
            ),
            "provenance": "SIMULATED",
        }

    @staticmethod
    def _fmt_hours(h: float) -> str:
        if h == float("inf"):
            return "indefinite"
        return f"{h:.0f} h ({h / 24:.1f} d)"

    @staticmethod
    def _autonomy_from_run(run, initial_fuel_l: float,
                           generators_available: int | None = None) -> float:
        """Worst-case autonomy across the simulated horizon.

        Measuring only the END state is misleading: a scenario that sheds
        load burns less fuel and therefore finishes with MORE apparent
        autonomy than normal operation, which would rank a generator failure
        as an improvement. The resilience question is how thin the margin got
        at its worst, so take the minimum over the horizon.
        """
        if not run.states:
            return 0.0
        worst = float("inf")
        for s in run.states:
            est = estimate_autonomy(
                critical_demand_kw=s.critical_load_kw,
                soc_pct=s.battery_soc_pct,
                fuel_l=s.fuel_level_l,
                ambient_c=s.temperature_c,
                renewable_kw=s.renewable_kw,
                generators_available=generators_available,
            )
            if est.hours < worst:
                worst = est.hours
        if worst == float("inf"):
            return 24 * 365.0
        return worst

    @staticmethod
    def _rate(autonomy: float, run, secured: bool, baseline_autonomy: float) -> str:
        """Rate the scenario against the baseline, not against absolutes.

        Note what is deliberately NOT a severity signal: the battery merely
        touching its protected floor. Under normal dispatch the pack cycles
        down to that floor and is recharged, so treating any contact with it
        as "severe" would rate flat-calm normal operation the same as a
        compound failure. What actually matters is unserved load, shedding,
        and how far autonomy collapsed relative to the baseline case.
        """
        if not secured or run.blackout:
            return "CATASTROPHIC"

        ratio = (autonomy / baseline_autonomy) if baseline_autonomy > 0 else 1.0
        shed = run.total_shed_kwh

        if (autonomy < THRESHOLDS.autonomy_critical_h
                or ratio < 0.5
                or shed > 250.0):
            return "SEVERE"
        if (autonomy < THRESHOLDS.autonomy_warning_h
                or ratio < 0.8
                or shed > 25.0):
            return "ELEVATED"
        return "MANAGEABLE"

    @staticmethod
    def _actions(d: ScenarioDefinition, run, autonomy: float) -> list[str]:
        actions: list[str] = []
        if d.operator_note:
            actions.append(d.operator_note)
        if run.total_shed_kwh > 0.1:
            actions.append(
                f"Automatic load shedding released {run.total_shed_kwh:.0f} kWh "
                "from P4/P3 circuits to protect life-critical supply."
            )
        if run.min_soc_pct <= 25.0:
            actions.append(
                f"Battery reached {run.min_soc_pct:.0f}% - below the 30% "
                "reserve band. Restrict discharge to life-critical load only."
            )
        if run.generator_starts > 0:
            actions.append(
                f"{run.generator_starts} generator start(s) over the horizon; "
                f"{run.generator_runtime_h:.0f} h runtime."
            )
        if run.final_fuel_l < FUEL.low_level_l:
            actions.append(
                f"Fuel down to {run.final_fuel_l:.0f} L - below the "
                f"{FUEL.low_level_l:.0f} L low-level mark. Review resupply "
                "window and ration generator hours."
            )
        if autonomy < THRESHOLDS.autonomy_critical_h:
            actions.append(
                f"Autonomy {autonomy:.0f} h is under the "
                f"{THRESHOLDS.autonomy_critical_h:.0f} h critical threshold - "
                "declare an energy emergency and shed to life-critical only."
            )
        if d.turbines_available == 0:
            actions.append(
                "With the turbine array offline the station has no renewable "
                "buffer; every kWh now comes from stored energy or diesel."
            )
        return actions
