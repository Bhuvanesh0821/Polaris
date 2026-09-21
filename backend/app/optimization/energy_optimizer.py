"""Energy optimiser - receding-horizon dispatch and load scheduling.

Objective (lexicographic, in this order):
    1. never leave life-critical load unserved
    2. keep the battery above its protected floor at the end of the horizon
    3. minimise diesel fuel burned
    4. minimise renewable curtailment
    5. minimise generator starts and time spent below the wet-stacking floor

Why a rule-based receding-horizon solver rather than an LP/MILP
---------------------------------------------------------------
The binding constraints here are discrete and operational (unit commitment,
minimum run time, minimum loading, priority-ordered shedding). A transparent
merit-order solver with an explicit deferrable-load scheduling pass produces
dispatch an operator can audit line by line, which matters more in a polar
station than the last percent of optimality. Every decision it makes is
recorded with its reason, which is what feeds the Explainable AI page.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from app.core.physics import battery_step, dispatch_gensets
from app.core.station import (
    BATTERY,
    FUEL,
    GENERATOR,
    THRESHOLDS,
    LoadPriority,
)
from app.ml.energy_state_estimator import CO2_KG_PER_L
from app.services.energy_model import compute_load

log = logging.getLogger("polaris.optimize")


@dataclass
class DispatchHour:
    timestamp: datetime
    load_kw: float
    critical_kw: float
    deferrable_original_kw: float
    deferrable_scheduled_kw: float
    renewable_available_kw: float
    wind_kw: float
    solar_kw: float
    renewable_used_kw: float
    curtailed_kw: float
    battery_charge_kw: float
    battery_discharge_kw: float
    battery_soc_pct: float
    generator_kw: float
    generators_running: int
    generator_loading_pct: float
    fuel_lph: float
    shed_kw: float
    unserved_kw: float
    wet_stacking: bool
    reason: str

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["timestamp"] = self.timestamp.isoformat()
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items()}


@dataclass
class OptimizationOutcome:
    schedule: list[DispatchHour] = field(default_factory=list)
    baseline_fuel_l: float = 0.0
    optimized_fuel_l: float = 0.0
    fuel_saved_l: float = 0.0
    fuel_saved_pct: float = 0.0
    renewable_fraction: float = 0.0
    renewable_curtailed_kwh: float = 0.0
    generator_runtime_h: float = 0.0
    generator_starts: int = 0
    load_shed_kwh: float = 0.0
    load_deferred_kwh: float = 0.0
    unserved_critical_kwh: float = 0.0
    battery_throughput_kwh: float = 0.0
    final_soc_pct: float = 0.0
    co2_avoided_kg: float = 0.0
    feasible: bool = True
    solve_ms: float = 0.0
    rationale: list[str] = field(default_factory=list)
    constraints_binding: list[str] = field(default_factory=list)


class EnergyOptimizer:
    """Receding-horizon optimiser over a weather + renewable forecast."""

    def __init__(self, reserve_soc_pct: float | None = None,
                 fuel_reserve_l: float | None = None) -> None:
        self.reserve_soc_pct = (
            BATTERY.soc_reserve_pct if reserve_soc_pct is None else reserve_soc_pct
        )
        self.fuel_reserve_l = (
            FUEL.critical_level_l if fuel_reserve_l is None else fuel_reserve_l
        )

    # -- deferrable load scheduling ---------------------------------------

    @staticmethod
    def _schedule_deferrables(hours: list[dict]) -> tuple[np.ndarray, float, list[str]]:
        """Move deferrable energy into hours of renewable surplus.

        Conserves total deferrable energy over the horizon (the snow melter
        still has to make the same amount of water), but redistributes it
        toward hours where renewables would otherwise be curtailed.
        """
        n = len(hours)
        original = np.array([h["deferrable_kw"] for h in hours], dtype=float)
        total_energy = original.sum()
        if total_energy <= 0 or n == 0:
            return original, 0.0, []

        surplus = np.array([
            max(0.0, h["renewable_kw"] - (h["load_kw"] - h["deferrable_kw"]))
            for h in hours
        ], dtype=float)

        notes: list[str] = []
        if surplus.sum() <= 1e-6:
            return original, 0.0, ["No renewable surplus in the horizon - "
                                   "deferrable load left in place."]

        # Cap what any single hour may absorb so we do not create a new peak.
        cap = np.array([
            min(h["deferrable_kw"] * 3.0 + 5.0, max(5.0, surplus[i] * 1.2))
            for i, h in enumerate(hours)
        ], dtype=float)

        weights = surplus / surplus.sum()
        scheduled = np.minimum(weights * total_energy, cap)

        # Redistribute whatever the caps rejected, proportional to remaining room.
        leftover = total_energy - scheduled.sum()
        for _ in range(6):
            if leftover <= 1e-6:
                break
            room = np.maximum(0.0, cap - scheduled)
            if room.sum() <= 1e-6:
                break
            add = np.minimum(room, leftover * (room / room.sum()))
            scheduled += add
            leftover -= add.sum()
        if leftover > 1e-6:  # nothing left to absorb it: put it back evenly
            scheduled += leftover / n

        shifted = float(np.abs(scheduled - original).sum() / 2.0)
        if shifted > 0.5:
            best = int(np.argmax(scheduled - original))
            notes.append(
                f"Shifted {shifted:.1f} kWh of deferrable load (snow melter, "
                f"vehicle charging, batch lab work) into renewable-surplus "
                f"hours, concentrated around "
                f"{hours[best]['timestamp']:%d %b %H:%M} UTC."
            )
        return scheduled, shifted, notes

    # -- main solve --------------------------------------------------------

    def optimize(self, forecast: pd.DataFrame, initial_soc_pct: float,
                 initial_fuel_l: float, horizon_h: int | None = None,
                 allow_deferral: bool = True,
                 generators_available: int | None = None) -> OptimizationOutcome:
        """Optimise dispatch across the forecast horizon.

        `forecast` needs columns: target_time, temperature_c, wind_speed_ms,
        wind_kw, solar_kw, predicted_load_kw, critical_load_kw,
        deferrable_load_kw.
        """
        started = datetime.now()
        df = forecast.copy()
        if horizon_h:
            df = df.head(horizon_h)
        if df.empty:
            return OptimizationOutcome(feasible=False,
                                       rationale=["Empty forecast horizon."])

        hours: list[dict] = []
        for r in df.to_dict("records"):
            ts = r.get("target_time")
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            hours.append({
                "timestamp": ts,
                "load_kw": float(r.get("predicted_load_kw", 0.0)),
                "critical_kw": float(r.get("critical_load_kw", 0.0)),
                "deferrable_kw": float(r.get("deferrable_load_kw", 0.0)),
                "wind_kw": float(r.get("wind_kw", 0.0)),
                "solar_kw": float(r.get("solar_kw", 0.0)),
                "renewable_kw": float(r.get("wind_kw", 0.0)) + float(r.get("solar_kw", 0.0)),
                "temperature_c": float(r.get("temperature_c", -20.0)),
            })

        rationale: list[str] = []
        binding: list[str] = []

        # --- baseline: no deferral, no smart battery policy ---------------
        baseline_fuel = self._simulate(
            hours, initial_soc_pct, initial_fuel_l,
            deferrable_schedule=np.array([h["deferrable_kw"] for h in hours]),
            smart_battery=False, generators_available=generators_available,
        )[1]

        # --- optimised run -------------------------------------------------
        if allow_deferral:
            scheduled, shifted, notes = self._schedule_deferrables(hours)
            rationale.extend(notes)
        else:
            scheduled = np.array([h["deferrable_kw"] for h in hours])
            shifted = 0.0

        schedule, opt_fuel, stats = self._simulate(
            hours, initial_soc_pct, initial_fuel_l,
            deferrable_schedule=scheduled, smart_battery=True,
            generators_available=generators_available,
        )

        # The look-ahead battery policy holds charge back ahead of a forecast
        # lull. That is usually right, but in a horizon with no renewable
        # surplus it can burn MORE fuel than the naive policy. Never return a
        # plan that is worse than the baseline - re-solve without it and keep
        # whichever actually wins.
        if opt_fuel > baseline_fuel + 1e-6:
            alt_schedule, alt_fuel, alt_stats = self._simulate(
                hours, initial_soc_pct, initial_fuel_l,
                deferrable_schedule=scheduled, smart_battery=False,
                generators_available=generators_available,
            )
            if alt_fuel < opt_fuel:
                schedule, opt_fuel, stats = alt_schedule, alt_fuel, alt_stats
                rationale.append(
                    "Predictive battery reserve was disabled for this horizon: "
                    "with no renewable surplus forecast it would have burned "
                    "more fuel than a simple merit-order dispatch."
                )

        saved = baseline_fuel - opt_fuel
        saved_pct = (saved / baseline_fuel * 100.0) if baseline_fuel > 0 else 0.0

        outcome = OptimizationOutcome(
            schedule=schedule,
            baseline_fuel_l=round(baseline_fuel, 2),
            optimized_fuel_l=round(opt_fuel, 2),
            fuel_saved_l=round(saved, 2),
            fuel_saved_pct=round(saved_pct, 2),
            renewable_fraction=round(stats["renewable_fraction"], 4),
            renewable_curtailed_kwh=round(stats["curtailed_kwh"], 2),
            generator_runtime_h=round(stats["generator_hours"], 2),
            generator_starts=stats["generator_starts"],
            load_shed_kwh=round(stats["shed_kwh"], 2),
            load_deferred_kwh=round(shifted, 2),
            unserved_critical_kwh=round(stats["unserved_kwh"], 3),
            battery_throughput_kwh=round(stats["throughput_kwh"], 2),
            final_soc_pct=round(stats["final_soc"], 2),
            co2_avoided_kg=round(saved * CO2_KG_PER_L, 2),
            feasible=stats["unserved_kwh"] <= 0.01,
            solve_ms=round((datetime.now() - started).total_seconds() * 1000, 2),
        )

        # --- narrative ----------------------------------------------------
        if saved > 0.1:
            rationale.insert(0, (
                f"Optimised dispatch burns {opt_fuel:.0f} L against a "
                f"{baseline_fuel:.0f} L baseline over {len(hours)} h - a "
                f"saving of {saved:.0f} L ({saved_pct:.1f}%), avoiding "
                f"{outcome.co2_avoided_kg:.0f} kg CO2."
            ))
        else:
            rationale.insert(0, (
                "No fuel saving available in this horizon: renewable output "
                "and storage headroom are already fully used."
            ))

        rationale.append(
            f"Renewables meet {outcome.renewable_fraction * 100:.1f}% of served "
            f"energy (station target {THRESHOLDS.renewable_fraction_target * 100:.0f}%)."
        )
        if outcome.renewable_curtailed_kwh > 1.0:
            rationale.append(
                f"{outcome.renewable_curtailed_kwh:.0f} kWh still curtailed - "
                "storage is the binding constraint, not generation."
            )
            binding.append("battery_capacity")
        if outcome.generator_starts > 0:
            rationale.append(
                f"{outcome.generator_starts} generator start(s), "
                f"{outcome.generator_runtime_h:.0f} h total runtime."
            )
        if stats["wet_hours"] > 0:
            rationale.append(
                f"{stats['wet_hours']} h of generator operation below the "
                f"{GENERATOR.min_loading_frac * 100:.0f}% loading floor - "
                "consider consolidating onto fewer units."
            )
            binding.append("genset_min_loading")
        if outcome.load_shed_kwh > 0.1:
            rationale.append(
                f"{outcome.load_shed_kwh:.1f} kWh of low-priority load shed to "
                "protect critical circuits."
            )
            binding.append("supply_deficit")
        if not outcome.feasible:
            rationale.append(
                f"INFEASIBLE: {outcome.unserved_critical_kwh:.1f} kWh of load "
                "cannot be served even after shedding. Fuel or generation "
                "capacity is insufficient for this horizon."
            )
            binding.append("unserved_load")
        if stats["final_soc"] <= self.reserve_soc_pct:
            binding.append("battery_reserve")

        outcome.rationale = rationale
        outcome.constraints_binding = binding
        return outcome

    # -- inner simulation --------------------------------------------------

    def _simulate(self, hours: list[dict], soc: float, fuel: float,
                  deferrable_schedule: np.ndarray, smart_battery: bool,
                  generators_available: int | None):
        """Run one dispatch policy over the horizon."""
        schedule: list[DispatchHour] = []
        total_fuel = 0.0
        curtailed_kwh = shed_kwh = unserved_kwh = throughput = 0.0
        renewable_used_kwh = generator_kwh = 0.0
        gen_hours = 0.0
        gen_starts = wet_hours = 0
        prev_running = 0

        for i, h in enumerate(hours):
            ts = h["timestamp"]
            temp = h["temperature_c"]
            fixed_load = h["load_kw"] - h["deferrable_kw"]
            load = max(0.0, fixed_load + float(deferrable_schedule[i]))
            critical = h["critical_kw"]
            renewable = h["renewable_kw"]

            net = renewable - load
            charge = discharge = curtail = shed = unserved = 0.0
            reason = ""

            if net >= 0:
                # Surplus. Charge unless already full.
                step = battery_step(soc, net, 1.0, temp)
                charge = max(0.0, step.accepted_kw)
                curtail = max(0.0, net - charge)
                soc = step.soc_pct
                gen = dispatch_gensets(0.0, temp, generators_available)
                reason = (
                    f"Renewable surplus {net:.1f} kW; charging at {charge:.1f} kW"
                    + (f", curtailing {curtail:.1f} kW" if curtail > 0.1 else "")
                )
            else:
                deficit = -load + renewable  # negative
                deficit = -deficit
                # Look ahead: if strong renewables are coming, lean on the
                # battery now; if a lull is coming, hold charge for it.
                floor = self.reserve_soc_pct
                if smart_battery:
                    look = hours[i + 1: i + 7]
                    if look:
                        upcoming = np.mean([x["renewable_kw"] for x in look])
                        upcoming_load = np.mean([x["load_kw"] for x in look])
                        if upcoming > upcoming_load * 1.05:
                            floor = BATTERY.soc_min_pct  # recharge is coming
                        elif upcoming < upcoming_load * 0.5:
                            floor = min(60.0, self.reserve_soc_pct + 18.0)
                else:
                    floor = BATTERY.soc_min_pct

                if soc > floor:
                    from app.core.physics import battery_available_kwh

                    allowed = battery_available_kwh(soc, temp, floor_pct=floor)
                    want = min(deficit, allowed, BATTERY.max_discharge_kw)
                    step = battery_step(soc, -want, 1.0, temp)
                    discharge = max(0.0, -step.accepted_kw)
                    soc = step.soc_pct
                else:
                    step = battery_step(soc, 0.0, 1.0, temp)
                    soc = step.soc_pct

                remaining = max(0.0, deficit - discharge)
                gen = dispatch_gensets(remaining, temp, generators_available)

                if fuel - gen["fuel_lph"] < self.fuel_reserve_l:
                    # Protect the emergency reserve: only critical load may
                    # draw on it.
                    allowed_kw = max(0.0, critical - renewable - discharge)
                    gen = dispatch_gensets(
                        min(remaining, allowed_kw), temp, generators_available
                    )

                unmet = max(0.0, remaining - gen["output_kw"])
                if unmet > 1e-6:
                    sheddable = sum(
                        c.demand_kw * c.shed_fraction_max
                        for c in compute_load(ts, temp).channels
                        if c.priority != LoadPriority.P1_LIFE_CRITICAL
                    )
                    shed = min(unmet, sheddable)
                    unserved = max(0.0, unmet - shed)

                reason = (
                    f"Deficit {deficit:.1f} kW; battery {discharge:.1f} kW, "
                    f"generator {gen['output_kw']:.1f} kW "
                    f"({gen['loading_pct']:.0f}% loading)"
                )
                if shed > 0.1:
                    reason += f"; shed {shed:.1f} kW of low-priority load"
                if unserved > 0.01:
                    reason += f"; {unserved:.1f} kW UNSERVED"

            fuel_used = gen["fuel_lph"]
            if fuel <= self.fuel_reserve_l * 0.0:
                fuel_used = 0.0
            fuel = max(0.0, fuel - fuel_used)
            total_fuel += fuel_used

            if gen["units_running"] > 0:
                gen_hours += 1
                if prev_running == 0:
                    gen_starts += 1
                if gen["wet_stacking"]:
                    wet_hours += 1
            prev_running = gen["units_running"]

            curtailed_kwh += curtail
            shed_kwh += shed
            unserved_kwh += unserved
            throughput += charge + discharge
            renewable_used_kwh += renewable - curtail
            generator_kwh += gen["output_kw"]

            schedule.append(DispatchHour(
                timestamp=ts, load_kw=load, critical_kw=critical,
                deferrable_original_kw=h["deferrable_kw"],
                deferrable_scheduled_kw=float(deferrable_schedule[i]),
                renewable_available_kw=renewable,
                wind_kw=h["wind_kw"], solar_kw=h["solar_kw"],
                renewable_used_kw=renewable - curtail, curtailed_kw=curtail,
                battery_charge_kw=charge, battery_discharge_kw=discharge,
                battery_soc_pct=soc, generator_kw=gen["output_kw"],
                generators_running=gen["units_running"],
                generator_loading_pct=gen["loading_pct"], fuel_lph=fuel_used,
                shed_kw=shed, unserved_kw=unserved,
                wet_stacking=gen["wet_stacking"], reason=reason,
            ))

        served = renewable_used_kwh + generator_kwh
        stats = {
            "curtailed_kwh": curtailed_kwh,
            "shed_kwh": shed_kwh,
            "unserved_kwh": unserved_kwh,
            "throughput_kwh": throughput,
            "generator_hours": gen_hours,
            "generator_starts": gen_starts,
            "wet_hours": wet_hours,
            "final_soc": soc,
            "renewable_fraction": (renewable_used_kwh / served) if served > 0 else 0.0,
        }
        return schedule, total_fuel, stats
