"""Energy state estimation.

Rolls the station energy balance forward hour by hour from an initial state,
driven by real weather. Produces the MODELLED battery SoC, fuel level,
generator state and served/shed load that the rest of POLARIS consumes.

This is a simulator, not a sensor. It is the module that makes the project's
data-honesty rule concrete: everything it emits is labelled MODELLED.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime

import pandas as pd

from app.core.physics import battery_step, dispatch_gensets
from app.core.station import BATTERY, FUEL, GENERATOR
from app.services.energy_model import compute_generation, compute_load

log = logging.getLogger("polaris.ml.state")


@dataclass
class EnergyState:
    """One hour of modelled station energy state."""

    timestamp: datetime

    total_load_kw: float
    critical_load_kw: float
    deferrable_load_kw: float
    served_load_kw: float
    shed_load_kw: float
    unserved_load_kw: float

    wind_generation_kw: float
    solar_generation_kw: float
    renewable_kw: float
    renewable_curtailed_kw: float

    generator_output_kw: float
    generators_running: int
    generator_loading_pct: float
    generator_efficiency: float
    wet_stacking: bool

    battery_soc_pct: float
    battery_stored_kwh: float
    battery_charge_kw: float
    battery_discharge_kw: float
    battery_mode: str

    fuel_consumed_l: float
    fuel_level_l: float

    renewable_fraction: float
    co2_kg: float
    temperature_c: float
    wind_speed_ms: float

    def as_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class SimulationResult:
    states: list[EnergyState] = field(default_factory=list)
    final_soc_pct: float = 0.0
    final_fuel_l: float = 0.0
    total_fuel_l: float = 0.0
    total_load_kwh: float = 0.0
    total_renewable_kwh: float = 0.0
    total_generator_kwh: float = 0.0
    total_shed_kwh: float = 0.0
    total_unserved_critical_kwh: float = 0.0
    renewable_fraction: float = 0.0
    generator_runtime_h: float = 0.0
    generator_starts: int = 0
    min_soc_pct: float = 100.0
    blackout: bool = False

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([s.as_dict() for s in self.states])


#: CO2 per litre of diesel burned (kg). Standard emission factor.
CO2_KG_PER_L = 2.68


class EnergyStateEstimator:
    """Hour-stepping energy balance with a merit-order dispatch rule.

    Dispatch order each hour:
        renewables -> battery (within SoC band) -> diesel -> load shedding
    Shedding walks the priority ladder P4 -> P3 -> P2 and never touches P1.
    """

    def __init__(self, reserve_soc_pct: float | None = None,
                 allow_shedding: bool = True) -> None:
        self.reserve_soc_pct = (
            BATTERY.soc_reserve_pct if reserve_soc_pct is None else reserve_soc_pct
        )
        self.allow_shedding = allow_shedding

    @staticmethod
    def _recharge_power(soc_pct: float, deficit_kw: float,
                        generators_available: int | None) -> float:
        """Extra generator output to put back into the battery.

        Sized so the fewest units that can carry the load run near their
        optimal loading point, rather than idling inefficiently at the exact
        deficit. Returns 0 once the pack is back inside its reserve band.
        """
        if soc_pct >= BATTERY.soc_reserve_pct:
            return 0.0
        n_avail = GENERATOR.n_units if generators_available is None else generators_available
        if n_avail <= 0:
            return 0.0

        rated = GENERATOR.rated_kw_each
        units = max(1, math.ceil(deficit_kw / (rated * GENERATOR.max_loading_frac)))
        units = min(units, n_avail)
        efficient_output = units * rated * GENERATOR.optimal_loading_frac
        spare = max(0.0, efficient_output - deficit_kw)
        return min(spare, BATTERY.max_charge_kw)

    def step(self, ts: datetime, weather_row: dict, soc_pct: float, fuel_l: float,
             prev_generators: int = 0, hours: float = 1.0,
             load_override_kw: float | None = None,
             turbines_available: int | None = None,
             generators_available: int | None = None,
             solar_available: bool = True,
             demand_multiplier: float = 1.0) -> EnergyState:
        """Advance the energy state by one timestep."""
        temp = float(weather_row.get("temperature_c", -20.0))
        wind = float(weather_row.get("wind_speed_ms", 0.0))
        chill = weather_row.get("wind_chill_c")
        polar_night = bool(weather_row.get("is_polar_night", False))

        # --- demand ---
        load = compute_load(ts, temp, wind_chill_c=chill, is_polar_night=polar_night,
                            wind_speed_ms=wind)
        total_load = (load_override_kw if load_override_kw is not None
                      else load.total_kw) * demand_multiplier
        critical = load.critical_kw * demand_multiplier
        deferrable = load.deferrable_kw * demand_multiplier

        # --- renewable supply ---
        gen = compute_generation(
            ts=ts, temperature_c=temp, wind_speed_ms=wind,
            solar_radiation_wm2=weather_row.get("solar_radiation_wm2"),
            humidity_pct=weather_row.get("humidity_pct"),
            cloud_cover_pct=weather_row.get("cloud_cover_pct"),
            air_density_kg_m3=weather_row.get("air_density_kg_m3"),
            pressure_hpa=weather_row.get("pressure_hpa"),
            direct_radiation_wm2=weather_row.get("direct_radiation_wm2"),
            diffuse_radiation_wm2=weather_row.get("diffuse_radiation_wm2"),
            snowfall_mm=weather_row.get("snowfall_mm"),
            turbines_available=turbines_available,
            solar_available=solar_available,
        )
        renewable = gen.total_kw

        net = renewable - total_load  # + surplus, - deficit
        curtailed = 0.0
        charge_kw = discharge_kw = 0.0
        battery_mode = "IDLE"
        stored = 0.0

        if net >= 0:
            # Surplus -> charge, curtail whatever the pack cannot take.
            step_res = battery_step(soc_pct, net, hours, temp)
            charge_kw = max(0.0, step_res.accepted_kw)
            curtailed = max(0.0, net - charge_kw)
            soc_pct = step_res.soc_pct
            stored = step_res.stored_kwh
            battery_mode = "CHARGING" if charge_kw > 0.1 else "IDLE"
            gen_dispatch = dispatch_gensets(0.0, temp, generators_available)
            served = total_load
            shed = unserved = 0.0
        else:
            deficit = -net
            usable_floor = max(BATTERY.soc_min_pct, self.reserve_soc_pct)

            if soc_pct > usable_floor:
                # Above the reserve band: lean on storage before burning fuel.
                step_res = battery_step(soc_pct, -deficit, hours, temp)
                discharge_kw = max(0.0, -step_res.accepted_kw)
                soc_pct = step_res.soc_pct
                stored = step_res.stored_kwh
                if soc_pct < usable_floor:
                    soc_pct = max(soc_pct, BATTERY.soc_min_pct)
                battery_mode = "DISCHARGING" if discharge_kw > 0.1 else "RESERVE"

                remaining = max(0.0, deficit - discharge_kw)
                gen_dispatch = dispatch_gensets(remaining, temp, generators_available)
                unmet = gen_dispatch["unmet_kw"]
            else:
                # At or below the reserve band. Do not draw the pack down
                # further - instead run the generator hard enough to carry the
                # load AND recharge. That is how a polar plant is actually
                # operated, and it is also where the genset burns least fuel
                # per kWh: a unit at 80 % loading uses ~0.295 L/kWh against
                # ~0.42 L/kWh at 30 %.
                recharge_request = self._recharge_power(
                    soc_pct, deficit, generators_available
                )
                gen_dispatch = dispatch_gensets(
                    deficit + recharge_request, temp, generators_available
                )
                gen_out = gen_dispatch["output_kw"]

                to_battery = max(0.0, gen_out - deficit)
                if to_battery > 0.01:
                    step_res = battery_step(soc_pct, to_battery, hours, temp)
                    charge_kw = max(0.0, step_res.accepted_kw)
                else:
                    step_res = battery_step(soc_pct, 0.0, hours, temp)
                    charge_kw = 0.0
                soc_pct = step_res.soc_pct
                stored = step_res.stored_kwh
                battery_mode = "CHARGING" if charge_kw > 0.1 else "RESERVE"

                remaining = deficit
                # Only a shortfall against the LOAD is unserved; falling short
                # of the recharge target is not.
                unmet = max(0.0, deficit - min(gen_out, deficit))

            shed = 0.0
            if unmet > 1e-6 and self.allow_shedding:
                # Shed from the bottom of the priority ladder upward.
                sheddable = 0.0
                for ch in sorted(load.channels,
                                 key=lambda c: c.priority.value, reverse=True):
                    if ch.priority.value.startswith("P1"):
                        continue
                    sheddable += ch.demand_kw * ch.shed_fraction_max
                shed = min(unmet, sheddable)
            served = total_load - shed
            unserved = max(0.0, unmet - shed)
            curtailed = 0.0

        generator_kw = gen_dispatch["output_kw"]
        fuel_used = gen_dispatch["fuel_lph"] * hours
        fuel_l = max(0.0, fuel_l - fuel_used)
        if fuel_l <= 0.0:
            generator_kw = 0.0
            fuel_used = 0.0

        supplied = renewable + generator_kw + discharge_kw
        renewable_fraction = (
            min(1.0, renewable / supplied) if supplied > 0 else 0.0
        )

        return EnergyState(
            timestamp=ts,
            total_load_kw=round(total_load, 3),
            critical_load_kw=round(critical, 3),
            deferrable_load_kw=round(deferrable, 3),
            served_load_kw=round(max(0.0, served), 3),
            shed_load_kw=round(shed, 3),
            unserved_load_kw=round(unserved, 3),
            wind_generation_kw=gen.wind_kw,
            solar_generation_kw=gen.solar_kw,
            renewable_kw=round(renewable, 3),
            renewable_curtailed_kw=round(curtailed, 3),
            generator_output_kw=round(generator_kw, 3),
            generators_running=gen_dispatch["units_running"],
            generator_loading_pct=gen_dispatch["loading_pct"],
            generator_efficiency=gen_dispatch["efficiency"],
            wet_stacking=gen_dispatch["wet_stacking"],
            battery_soc_pct=round(soc_pct, 3),
            battery_stored_kwh=round(stored, 3),
            battery_charge_kw=round(charge_kw, 3),
            battery_discharge_kw=round(discharge_kw, 3),
            battery_mode=battery_mode,
            fuel_consumed_l=round(fuel_used, 4),
            fuel_level_l=round(fuel_l, 2),
            renewable_fraction=round(renewable_fraction, 4),
            co2_kg=round(fuel_used * CO2_KG_PER_L, 3),
            temperature_c=round(temp, 2),
            wind_speed_ms=round(wind, 2),
        )

    def run(self, weather: pd.DataFrame, initial_soc_pct: float = 72.0,
            initial_fuel_l: float | None = None, **step_kwargs) -> SimulationResult:
        """Run the balance over a whole weather frame."""
        soc = initial_soc_pct
        fuel = FUEL.initial_level_l if initial_fuel_l is None else initial_fuel_l
        prev_running = 0
        result = SimulationResult()

        for row in weather.to_dict("records"):
            ts = row.get("observed_at")
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            elif hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()

            state = self.step(ts, row, soc, fuel, prev_generators=prev_running,
                              **step_kwargs)
            soc, fuel = state.battery_soc_pct, state.fuel_level_l

            if state.generators_running > 0 and prev_running == 0:
                result.generator_starts += 1
            if state.generators_running > 0:
                result.generator_runtime_h += 1.0
            prev_running = state.generators_running

            result.states.append(state)
            result.total_fuel_l += state.fuel_consumed_l
            result.total_load_kwh += state.total_load_kw
            result.total_renewable_kwh += state.renewable_kw - state.renewable_curtailed_kw
            result.total_generator_kwh += state.generator_output_kw
            result.total_shed_kwh += state.shed_load_kw
            result.min_soc_pct = min(result.min_soc_pct, state.battery_soc_pct)
            if state.unserved_load_kw > 0:
                if state.unserved_load_kw > 0.5:
                    result.blackout = True
                result.total_unserved_critical_kwh += state.unserved_load_kw

        result.final_soc_pct = soc
        result.final_fuel_l = fuel
        served = result.total_renewable_kwh + result.total_generator_kwh
        result.renewable_fraction = (
            round(result.total_renewable_kwh / served, 4) if served > 0 else 0.0
        )
        return result


def summarise_state(state: EnergyState) -> dict:
    """Flat dashboard-friendly summary of one modelled hour."""
    return {
        "timestamp": state.timestamp.isoformat(),
        "total_load_kw": state.total_load_kw,
        "critical_load_kw": state.critical_load_kw,
        "renewable_kw": state.renewable_kw,
        "wind_kw": state.wind_generation_kw,
        "solar_kw": state.solar_generation_kw,
        "generator_kw": state.generator_output_kw,
        "generators_running": state.generators_running,
        "battery_soc_pct": state.battery_soc_pct,
        "battery_mode": state.battery_mode,
        "fuel_level_l": state.fuel_level_l,
        "fuel_level_pct": round(state.fuel_level_l / FUEL.tank_capacity_l * 100, 2),
        "renewable_fraction": state.renewable_fraction,
        "shed_load_kw": state.shed_load_kw,
        "provenance": "MODELLED",
    }
