"""The RESEARCH-BASED ENERGY MODEL.

Converts real measured weather into an estimate of what the station's
electrical system is doing. Every number produced here is MODELLED.

Load model
----------
Each channel in core.station.LOAD_CHANNELS is:

    kW = base
       + diurnal * activity(hour)
       + hdd_coeff * max(0, balance_point - T_effective)
       + occupancy_coeff * crew

T_effective uses wind chill rather than dry-bulb temperature, because at
Maitri a 20 m/s katabatic wind at -25 C drives far more building heat loss
than still air at the same temperature.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.physics import (
    air_density as physics_air_density,
    battery_available_kwh,
    dispatch_gensets,
    solar_power_kw,
    wind_chill_c as physics_wind_chill,
    wind_power_kw,
)
from app.core.station import (
    FUEL,
    GENERATOR,
    HEATING_BALANCE_POINT_C,
    LOAD_CHANNELS,
    SITE,
    LoadPriority,
)


# ---------------------------------------------------------------------------
# Occupancy
# ---------------------------------------------------------------------------


def _present(value) -> float | None:
    """Treat None *and* NaN as 'not reported'.

    A NaN that slips through silently collapses max(0, NaN) to 0.0, which
    would zero out heating load or turbine output without any error. Missing
    inputs must be recomputed from first principles, never treated as zero.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def crew_size(ts: datetime) -> int:
    """Antarctic stations swing between a large summer and small winter crew.

    The austral summer season runs roughly November to March; the changeover
    is modelled as a smooth ramp rather than a step.
    """
    doy = ts.timetuple().tm_yday
    # Peak summer ~ 1 January (doy 1), deep winter ~ 1 July (doy 182)
    phase = math.cos(2 * math.pi * (doy - 1) / 365.25)
    frac = (phase + 1.0) / 2.0  # 1.0 at midsummer, 0.0 at midwinter
    return int(round(SITE.winter_crew + (SITE.summer_crew - SITE.winter_crew) * frac))


def _activity(hour: int, peak_hour: int) -> float:
    """Smooth 0..1 diurnal activity factor peaking at `peak_hour`."""
    delta = (hour - peak_hour) % 24
    if delta > 12:
        delta -= 24
    return 0.5 * (1.0 + math.cos(math.pi * delta / 12.0))


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


@dataclass
class ChannelLoad:
    key: str
    label: str
    priority: LoadPriority
    demand_kw: float
    is_deferrable: bool
    shed_fraction_max: float


@dataclass
class StationLoad:
    timestamp: datetime
    total_kw: float
    critical_kw: float  # P1 + P2
    life_critical_kw: float  # P1 only
    deferrable_kw: float  # P4
    heating_kw: float
    crew: int
    effective_temp_c: float
    channels: list[ChannelLoad] = field(default_factory=list)

    def by_priority(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for c in self.channels:
            out[c.priority.value] = out.get(c.priority.value, 0.0) + c.demand_kw
        return {k: round(v, 3) for k, v in out.items()}


def compute_load(ts: datetime, temperature_c: float,
                 wind_chill_c: float | None = None,
                 is_polar_night: bool = False,
                 wind_speed_ms: float | None = None) -> StationLoad:
    """Estimate station electrical demand for one hour. MODELLED."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    hour = ts.hour
    crew = crew_size(ts)

    temp = _present(temperature_c)
    if temp is None:
        raise ValueError("compute_load requires a measured temperature")

    # Building heat loss follows wind chill, not dry-bulb temperature.
    t_eff = _present(wind_chill_c)
    if t_eff is None:
        wind = _present(wind_speed_ms)
        t_eff = physics_wind_chill(temp, wind) if wind is not None else temp
    hdd = max(0.0, HEATING_BALANCE_POINT_C - t_eff)

    channels: list[ChannelLoad] = []
    total = critical = life_critical = deferrable = heating = 0.0

    for ch in LOAD_CHANNELS:
        kw = ch.base_kw
        kw += ch.diurnal_kw * _activity(hour, ch.peak_hour)
        kw += ch.hdd_kw_per_c * hdd
        kw += ch.occupancy_kw_per_person * crew

        # Polar night: lighting runs continuously, outdoor work drops off.
        if is_polar_night:
            if ch.key == "lighting_accom":
                kw *= 1.35
            elif ch.key in ("workshop", "vehicle_charging"):
                kw *= 0.65

        kw = max(0.0, round(kw, 4))
        channels.append(ChannelLoad(
            key=ch.key, label=ch.label, priority=ch.priority, demand_kw=kw,
            is_deferrable=ch.deferrable, shed_fraction_max=ch.shed_fraction_max,
        ))
        total += kw
        if ch.priority in (LoadPriority.P1_LIFE_CRITICAL,
                           LoadPriority.P2_SCIENCE_CRITICAL):
            critical += kw
        if ch.priority == LoadPriority.P1_LIFE_CRITICAL:
            life_critical += kw
        if ch.priority == LoadPriority.P4_DEFERRABLE:
            deferrable += kw
        if ch.key == "space_heating":
            heating += kw

    return StationLoad(
        timestamp=ts, total_kw=round(total, 3), critical_kw=round(critical, 3),
        life_critical_kw=round(life_critical, 3),
        deferrable_kw=round(deferrable, 3), heating_kw=round(heating, 3),
        crew=crew, effective_temp_c=round(float(t_eff), 2), channels=channels,
    )


# ---------------------------------------------------------------------------
# Generation from measured weather
# ---------------------------------------------------------------------------


@dataclass
class Generation:
    wind_kw: float
    solar_kw: float
    total_kw: float
    wind_detail: dict
    solar_detail: dict


def compute_generation(ts: datetime, temperature_c: float, wind_speed_ms: float,
                       solar_radiation_wm2: float | None = None,
                       humidity_pct: float | None = None,
                       cloud_cover_pct: float | None = None,
                       air_density_kg_m3: float | None = None,
                       pressure_hpa: float | None = None,
                       direct_radiation_wm2: float | None = None,
                       diffuse_radiation_wm2: float | None = None,
                       snowfall_mm: float | None = None,
                       turbines_available: int | None = None,
                       solar_available: bool = True) -> Generation:
    """Estimate renewable output from measured weather. MODELLED.

    Any derived input that arrives missing or NaN is recomputed here rather
    than defaulted, so a gap in one field cannot silently zero out generation.
    """
    temp = _present(temperature_c)
    wind = _present(wind_speed_ms)
    if temp is None or wind is None:
        raise ValueError(
            "compute_generation requires measured temperature and wind speed"
        )

    rho = _present(air_density_kg_m3)
    if rho is None:
        rho = physics_air_density(temp, _present(pressure_hpa))

    wind_kw, wind_detail = wind_power_kw(
        wind, air_density_kg_m3=rho,
        temp_c=temp, humidity_pct=_present(humidity_pct),
        n_available=turbines_available,
    )
    if solar_available:
        dhi = _present(diffuse_radiation_wm2)
        dni_h = _present(direct_radiation_wm2)
        solar_kw, solar_detail = solar_power_kw(
            ts, _present(solar_radiation_wm2), temp, dhi=dhi, dni_h=dni_h,
            cloud_pct=_present(cloud_cover_pct),
            snow_cover=bool((_present(snowfall_mm) or 0.0) > 1.0),
        )
    else:
        solar_kw, solar_detail = 0.0, {"reason": "array offline"}

    return Generation(
        wind_kw=wind_kw, solar_kw=solar_kw,
        total_kw=round(wind_kw + solar_kw, 3),
        wind_detail=wind_detail, solar_detail=solar_detail,
    )


# ---------------------------------------------------------------------------
# Autonomy / survival
# ---------------------------------------------------------------------------


@dataclass
class AutonomyEstimate:
    hours: float
    battery_hours: float
    fuel_hours: float
    renewable_contribution_kw: float
    critical_demand_kw: float
    battery_available_kwh: float
    fuel_available_l: float
    fuel_usable_l: float
    limited_by: str
    detail: dict = field(default_factory=dict)


def estimate_autonomy(critical_demand_kw: float, soc_pct: float, fuel_l: float,
                      ambient_c: float, renewable_kw: float = 0.0,
                      generators_available: int | None = None,
                      respect_fuel_reserve: bool = True) -> AutonomyEstimate:
    """How long critical load can be held with what is on hand. MODELLED.

    Renewable output offsets demand first; the shortfall is covered by the
    battery down to its protected floor, then by generators until fuel hits
    the emergency reserve.
    """
    net_deficit = max(0.0, critical_demand_kw - renewable_kw)
    batt_kwh = battery_available_kwh(soc_pct, ambient_c)

    usable_fuel = max(0.0, fuel_l - (FUEL.critical_level_l if respect_fuel_reserve else 0.0))
    n_gen = GENERATOR.n_units if generators_available is None else generators_available

    if net_deficit <= 1e-6:
        return AutonomyEstimate(
            hours=float("inf"), battery_hours=float("inf"), fuel_hours=float("inf"),
            renewable_contribution_kw=round(renewable_kw, 2),
            critical_demand_kw=round(critical_demand_kw, 2),
            battery_available_kwh=round(batt_kwh, 2),
            fuel_available_l=round(fuel_l, 1), fuel_usable_l=round(usable_fuel, 1),
            limited_by="none - renewables cover critical load",
            detail={"net_deficit_kw": 0.0},
        )

    battery_hours = batt_kwh / net_deficit if net_deficit > 0 else 0.0

    if n_gen <= 0 or usable_fuel <= 0:
        fuel_hours = 0.0
    else:
        dispatch = dispatch_gensets(net_deficit, ambient_c, units_available=n_gen)
        lph = dispatch["fuel_lph"]
        fuel_hours = usable_fuel / lph if lph > 0 else 0.0

    total = battery_hours + fuel_hours
    limited_by = "battery only (no generator fuel)" if fuel_hours <= 0 else (
        "fuel reserve" if fuel_hours > battery_hours else "battery capacity"
    )

    return AutonomyEstimate(
        hours=round(total, 2),
        battery_hours=round(battery_hours, 2),
        fuel_hours=round(fuel_hours, 2),
        renewable_contribution_kw=round(renewable_kw, 2),
        critical_demand_kw=round(critical_demand_kw, 2),
        battery_available_kwh=round(batt_kwh, 2),
        fuel_available_l=round(fuel_l, 1),
        fuel_usable_l=round(usable_fuel, 1),
        limited_by=limited_by,
        detail={
            "net_deficit_kw": round(net_deficit, 3),
            "generators_available": n_gen,
            "fuel_reserve_held_l": FUEL.critical_level_l if respect_fuel_reserve else 0.0,
        },
    )


@dataclass
class AutonomyModes:
    """Autonomy under three operating postures.

    normal         everything stays on - what the station has right now
    critical_only  P3/P4 shed, only life- and science-critical load served
    crisis         critical load only AND no renewable contribution, i.e. a
                   storm has taken the turbines out and the sun is down. This
                   is the figure that should size the emergency reserve.
    """

    normal: AutonomyEstimate
    critical_only: AutonomyEstimate
    crisis: AutonomyEstimate

    @staticmethod
    def _h(est: AutonomyEstimate) -> float | None:
        return None if est.hours == float("inf") else round(est.hours, 2)

    def as_dict(self) -> dict:
        return {
            "normal_hours": self._h(self.normal),
            "critical_only_hours": self._h(self.critical_only),
            "crisis_hours": self._h(self.crisis),
            "normal": {
                "hours": self._h(self.normal),
                "demand_kw": self.normal.critical_demand_kw,
                "renewable_kw": self.normal.renewable_contribution_kw,
                "limited_by": self.normal.limited_by,
                "label": "Normal operation",
                "description": "All circuits served, current renewable output held.",
            },
            "critical_only": {
                "hours": self._h(self.critical_only),
                "demand_kw": self.critical_only.critical_demand_kw,
                "renewable_kw": self.critical_only.renewable_contribution_kw,
                "limited_by": self.critical_only.limited_by,
                "label": "Critical load only",
                "description": "P3 operational and P4 deferrable circuits shed.",
            },
            "crisis": {
                "hours": self._h(self.crisis),
                "demand_kw": self.crisis.critical_demand_kw,
                "renewable_kw": 0.0,
                "limited_by": self.crisis.limited_by,
                "label": "Crisis (no renewables)",
                "description": "Critical load only with zero renewable input - "
                               "turbines feathered and no sun.",
            },
        }


def autonomy_modes(total_demand_kw: float, critical_demand_kw: float,
                   soc_pct: float, fuel_l: float, ambient_c: float,
                   renewable_kw: float = 0.0,
                   generators_available: int | None = None) -> AutonomyModes:
    """Autonomy under normal, critical-only and crisis postures. MODELLED."""
    return AutonomyModes(
        normal=estimate_autonomy(
            critical_demand_kw=total_demand_kw, soc_pct=soc_pct, fuel_l=fuel_l,
            ambient_c=ambient_c, renewable_kw=renewable_kw,
            generators_available=generators_available,
        ),
        critical_only=estimate_autonomy(
            critical_demand_kw=critical_demand_kw, soc_pct=soc_pct, fuel_l=fuel_l,
            ambient_c=ambient_c, renewable_kw=renewable_kw,
            generators_available=generators_available,
        ),
        crisis=estimate_autonomy(
            critical_demand_kw=critical_demand_kw, soc_pct=soc_pct, fuel_l=fuel_l,
            ambient_c=ambient_c, renewable_kw=0.0,
            generators_available=generators_available,
        ),
    )


def initial_energy_state() -> dict:
    """Starting point for a fresh simulation horizon. MODELLED."""
    return {
        "soc_pct": 72.0,
        "fuel_l": FUEL.initial_level_l,
        "generator_running": 0,
        "generator_hours": 4120.0,
    }
