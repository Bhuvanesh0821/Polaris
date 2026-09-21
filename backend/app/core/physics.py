"""Deterministic physics for the POLARIS energy model.

Everything here is a RESEARCH-BASED MODEL. The *inputs* are real measured
weather; the *outputs* (generation, fuel burn, battery flow) are estimates.

Implemented
-----------
* solar position by vector geometry (hemisphere-safe)
* clear-sky irradiance + cloud attenuation, used when a source reports cloud
  cover but no radiation (e.g. a WMO SYNOP bulletin)
* plane-of-array transposition for a tilted, snow-surrounded PV array
* PV output with cell-temperature derating
* wind turbine power curve with air-density correction and icing derate
* diesel Willans-line fuel model
* battery charge/discharge step with temperature derating
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.station import BATTERY, FUEL, GENERATOR, SITE, SOLAR, WIND

SOLAR_CONSTANT = 1361.0  # W/m2


# ---------------------------------------------------------------------------
# Derived air properties
# ---------------------------------------------------------------------------


def wind_chill_c(temp_c: float, wind_ms: float) -> float:
    """Environment Canada / NWS wind chill index. Valid for T <= 10 C."""
    v_kmh = wind_ms * 3.6
    if temp_c > 10.0 or v_kmh < 4.8:
        return round(temp_c, 2)
    v16 = v_kmh**0.16
    return round(13.12 + 0.6215 * temp_c - 11.37 * v16 + 0.3965 * temp_c * v16, 2)


def air_density(temp_c: float, pressure_hpa: float | None = None) -> float:
    """Dry-air density, kg/m3.

    Cold polar air is dense: at -30 C and 985 hPa it is about 20 % denser than
    ISA sea level, which raises turbine output for the same wind speed by the
    same proportion. This is a genuine and significant polar effect.
    """
    p_pa = (pressure_hpa if pressure_hpa else 985.0) * 100.0
    t_k = temp_c + 273.15
    return round(p_pa / (287.05 * max(t_k, 150.0)), 4)


# ---------------------------------------------------------------------------
# Solar geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SolarPosition:
    elevation_deg: float
    zenith_deg: float
    azimuth_deg: float
    cos_zenith: float
    is_daylight: bool
    air_mass: float | None


def solar_declination(ts: datetime) -> float:
    """Cooper's equation, degrees."""
    n = ts.timetuple().tm_yday
    return 23.45 * math.sin(math.radians(360.0 * (284 + n) / 365.0))


def equation_of_time(ts: datetime) -> float:
    """Minutes. Spencer's approximation."""
    n = ts.timetuple().tm_yday
    b = 2 * math.pi * (n - 1) / 365.0
    return 229.18 * (
        0.000075 + 0.001868 * math.cos(b) - 0.032077 * math.sin(b)
        - 0.014615 * math.cos(2 * b) - 0.040849 * math.sin(2 * b)
    )


def solar_position(ts: datetime, latitude: float | None = None,
                   longitude: float | None = None) -> SolarPosition:
    """Sun position from a UTC instant, using an ENU unit-vector formulation
    so it stays correct at southern polar latitudes."""
    lat = SITE.latitude if latitude is None else latitude
    lon = SITE.longitude if longitude is None else longitude
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.astimezone(timezone.utc)

    decl = math.radians(solar_declination(ts))
    phi = math.radians(lat)

    utc_hours = ts.hour + ts.minute / 60.0 + ts.second / 3600.0
    solar_time = utc_hours + lon / 15.0 + equation_of_time(ts) / 60.0
    hour_angle = math.radians(15.0 * (solar_time - 12.0))

    sin_d, cos_d = math.sin(decl), math.cos(decl)
    sin_p, cos_p = math.sin(phi), math.cos(phi)
    cos_h, sin_h = math.cos(hour_angle), math.sin(hour_angle)

    up = sin_p * sin_d + cos_p * cos_d * cos_h
    north = cos_p * sin_d - sin_p * cos_d * cos_h
    east = -cos_d * sin_h

    up = max(-1.0, min(1.0, up))
    elevation = math.degrees(math.asin(up))
    zenith = 90.0 - elevation
    azimuth = (math.degrees(math.atan2(east, north)) + 360.0) % 360.0

    air_mass = None
    if elevation > 0.5:
        # Kasten-Young, valid down to the horizon
        air_mass = 1.0 / (
            math.sin(math.radians(elevation))
            + 0.50572 * (elevation + 6.07995) ** -1.6364
        )
    return SolarPosition(
        elevation_deg=round(elevation, 3),
        zenith_deg=round(zenith, 3),
        azimuth_deg=round(azimuth, 2),
        cos_zenith=max(0.0, up),
        is_daylight=elevation > 0.0,
        air_mass=round(air_mass, 3) if air_mass else None,
    )


def is_polar_night(ts: datetime, latitude: float | None = None) -> bool:
    """True when the sun stays below the horizon for the whole UTC day."""
    lat = SITE.latitude if latitude is None else latitude
    day = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    best = -90.0
    for h in range(0, 24, 2):
        pos = solar_position(day.replace(hour=h), latitude=lat)
        best = max(best, pos.elevation_deg)
    return best <= 0.0


def clear_sky_ghi(pos: SolarPosition) -> float:
    """Haurwitz clear-sky global horizontal irradiance, W/m2."""
    if not pos.is_daylight or pos.cos_zenith <= 0:
        return 0.0
    return max(0.0, 1098.0 * pos.cos_zenith * math.exp(-0.059 / pos.cos_zenith))


def ghi_from_cloud_cover(pos: SolarPosition, cloud_pct: float | None) -> float:
    """Kasten-Czeplak cloud attenuation of clear-sky GHI.

    Used only when the reporting source gives cloud cover but no radiation
    (a SYNOP bulletin). The result is an ESTIMATE and is labelled as such.
    """
    cs = clear_sky_ghi(pos)
    if cs <= 0:
        return 0.0
    c = 0.0 if cloud_pct is None else max(0.0, min(100.0, cloud_pct)) / 100.0
    return max(0.0, cs * (1.0 - 0.75 * c**3.4))


def erbs_diffuse_fraction(ghi: float, pos: SolarPosition) -> float:
    """Erbs correlation: diffuse fraction from the clearness index."""
    if ghi <= 0 or pos.cos_zenith <= 0:
        return 1.0
    extra = SOLAR_CONSTANT * pos.cos_zenith
    if extra <= 0:
        return 1.0
    kt = max(0.0, min(1.0, ghi / extra))
    if kt <= 0.22:
        return 1.0 - 0.09 * kt
    if kt <= 0.80:
        return (0.9511 - 0.1604 * kt + 4.388 * kt**2
                - 16.638 * kt**3 + 12.336 * kt**4)
    return 0.165


def plane_of_array(pos: SolarPosition, ghi: float,
                   dhi: float | None = None, dni_horizontal: float | None = None,
                   tilt_deg: float | None = None, azimuth_deg: float | None = None,
                   albedo: float | None = None) -> float:
    """Transpose horizontal irradiance onto the tilted array (isotropic sky)."""
    if ghi <= 0 or not pos.is_daylight:
        return 0.0
    tilt = math.radians(SOLAR.tilt_deg if tilt_deg is None else tilt_deg)
    # Southern hemisphere: the array faces true north (azimuth 0).
    surf_az = math.radians(SOLAR.azimuth_deg if azimuth_deg is None else azimuth_deg)
    rho = SOLAR.ground_albedo if albedo is None else albedo

    if dhi is None:
        dhi = ghi * erbs_diffuse_fraction(ghi, pos)
    beam_h = dni_horizontal if dni_horizontal is not None else max(0.0, ghi - dhi)

    # Sun and surface-normal unit vectors in (East, North, Up)
    zen = math.radians(pos.zenith_deg)
    sun_az = math.radians(pos.azimuth_deg)
    s = (math.sin(zen) * math.sin(sun_az),
         math.sin(zen) * math.cos(sun_az),
         math.cos(zen))
    n = (math.sin(tilt) * math.sin(surf_az),
         math.sin(tilt) * math.cos(surf_az),
         math.cos(tilt))
    cos_aoi = max(0.0, sum(a * b for a, b in zip(s, n)))

    dni = beam_h / pos.cos_zenith if pos.cos_zenith > 0.03 else 0.0
    poa_beam = dni * cos_aoi
    poa_diffuse = dhi * (1.0 + math.cos(tilt)) / 2.0
    poa_reflected = ghi * rho * (1.0 - math.cos(tilt)) / 2.0
    return max(0.0, poa_beam + poa_diffuse + poa_reflected)


def solar_power_kw(ts: datetime, ghi: float | None, temp_c: float,
                   dhi: float | None = None, dni_h: float | None = None,
                   cloud_pct: float | None = None,
                   snow_cover: bool = False) -> tuple[float, dict]:
    """Modelled PV output. Returns (kW, diagnostic detail)."""
    pos = solar_position(ts)
    estimated_input = False
    if ghi is None:
        ghi = ghi_from_cloud_cover(pos, cloud_pct)
        estimated_input = True

    detail = {
        "solar_elevation_deg": pos.elevation_deg,
        "solar_azimuth_deg": pos.azimuth_deg,
        "ghi_wm2": round(ghi, 1),
        "ghi_input_estimated": estimated_input,
        "is_daylight": pos.is_daylight,
    }
    if ghi <= 0 or not pos.is_daylight:
        detail.update({"poa_wm2": 0.0, "cell_temp_c": temp_c, "reason": "no irradiance"})
        return 0.0, detail

    poa = plane_of_array(pos, ghi, dhi=dhi, dni_horizontal=dni_h)
    cell_temp = temp_c + (SOLAR.noct_c - 20.0) / 800.0 * poa
    temp_factor = 1.0 + SOLAR.temp_coeff_per_c * (cell_temp - 25.0)
    soiling = SOLAR.soiling_snow_loss + (0.25 if snow_cover else 0.0)

    kw = (
        SOLAR.rated_kwp
        * (poa / 1000.0)
        * SOLAR.system_efficiency
        * max(0.0, temp_factor)
        * (1.0 - min(0.9, soiling))
        * SOLAR.availability
    )
    kw = max(0.0, min(kw, SOLAR.rated_kwp))
    detail.update({
        "poa_wm2": round(poa, 1),
        "cell_temp_c": round(cell_temp, 2),
        "temp_factor": round(temp_factor, 4),
        "soiling_loss": round(soiling, 3),
    })
    return round(kw, 3), detail


# ---------------------------------------------------------------------------
# Wind
# ---------------------------------------------------------------------------


def extrapolate_wind(speed_10m: float, hub_height: float | None = None) -> float:
    """Log-law extrapolation from anemometer height to hub height."""
    hub = WIND.hub_height_m if hub_height is None else hub_height
    z0 = WIND.roughness_length_m
    if speed_10m <= 0:
        return 0.0
    ratio = math.log(hub / z0) / math.log(WIND.anemometer_height_m / z0)
    return speed_10m * ratio


def wind_power_kw(wind_speed_10m: float, air_density_kg_m3: float | None = None,
                  temp_c: float | None = None, humidity_pct: float | None = None,
                  n_available: int | None = None) -> tuple[float, dict]:
    """Modelled wind farm output. Returns (kW, diagnostic detail).

    Uses a cubic ramp between cut-in and rated, corrected for air density -
    at -30 C polar air is roughly 25 % denser than ISA, which is a real and
    significant uplift in turbine output.
    """
    n = WIND.n_turbines if n_available is None else n_available
    v_hub = extrapolate_wind(wind_speed_10m)
    rho = air_density_kg_m3 or 1.225
    rho_factor = rho / 1.225

    icing = bool(
        temp_c is not None and humidity_pct is not None
        and WIND.icing_temp_band_c[0] <= temp_c <= WIND.icing_temp_band_c[1]
        and humidity_pct >= WIND.icing_humidity_pct
    )

    detail = {
        "wind_10m_ms": round(wind_speed_10m, 2),
        "wind_hub_ms": round(v_hub, 2),
        "air_density": round(rho, 4),
        "density_factor": round(rho_factor, 4),
        "icing_risk": icing,
        "turbines_available": n,
        "curtailed": False,
    }

    if v_hub < WIND.cut_in_ms:
        detail["reason"] = "below cut-in"
        return 0.0, detail
    if v_hub >= WIND.cut_out_ms:
        detail.update({"reason": "storm cut-out", "curtailed": True})
        return 0.0, detail

    if v_hub >= WIND.rated_ms:
        per_turbine = WIND.rated_kw_each
    else:
        span = WIND.rated_ms**3 - WIND.cut_in_ms**3
        frac = (v_hub**3 - WIND.cut_in_ms**3) / span if span > 0 else 0.0
        per_turbine = WIND.rated_kw_each * max(0.0, min(1.0, frac))

    kw = per_turbine * n * rho_factor * WIND.availability
    if icing:
        kw *= WIND.icing_derate
    kw = max(0.0, min(kw, WIND.rated_kw_each * n))
    detail["per_turbine_kw"] = round(per_turbine, 2)
    return round(kw, 3), detail


# ---------------------------------------------------------------------------
# Diesel generator
# ---------------------------------------------------------------------------


def genset_fuel_lph(output_kw: float, rated_kw: float | None = None,
                    ambient_c: float | None = None) -> float:
    """Willans line: fuel_lph = c0*rated + c1*output, plus a deep-cold penalty."""
    rated = GENERATOR.rated_kw_each if rated_kw is None else rated_kw
    if output_kw <= 0:
        return 0.0
    lph = GENERATOR.willans_c0 * rated + GENERATOR.willans_c1 * output_kw
    if ambient_c is not None and ambient_c < GENERATOR.cold_start_penalty_below_c:
        lph *= 1.0 + GENERATOR.cold_start_penalty_frac
    return round(lph, 4)


def genset_efficiency(output_kw: float, rated_kw: float | None = None,
                      ambient_c: float | None = None) -> float:
    """Electrical efficiency as a fraction (output energy / fuel energy)."""
    lph = genset_fuel_lph(output_kw, rated_kw, ambient_c)
    if lph <= 0:
        return 0.0
    fuel_kw = lph * FUEL.energy_kwh_per_l
    return round(output_kw / fuel_kw, 4) if fuel_kw > 0 else 0.0


def dispatch_gensets(required_kw: float, ambient_c: float | None = None,
                     units_available: int | None = None) -> dict:
    """Choose how many gensets to run and at what loading.

    Prefers the fewest units that keep loading above the wet-stacking floor -
    running one machine at 80 % burns far less fuel than two at 40 %.
    """
    n_avail = GENERATOR.n_units if units_available is None else units_available
    rated = GENERATOR.rated_kw_each
    if required_kw <= 0 or n_avail <= 0:
        return {"units_running": 0, "output_kw": 0.0, "fuel_lph": 0.0,
                "loading_pct": 0.0, "efficiency": 0.0, "wet_stacking": False,
                "unmet_kw": max(0.0, required_kw), "per_unit_kw": 0.0}

    max_out = n_avail * rated * GENERATOR.max_loading_frac
    target = min(required_kw, max_out)

    best = None
    for n in range(1, n_avail + 1):
        capacity = n * rated * GENERATOR.max_loading_frac
        if capacity < target - 1e-6 and n < n_avail:
            continue
        per_unit = min(target / n, rated * GENERATOR.max_loading_frac)
        loading = per_unit / rated
        lph = n * genset_fuel_lph(per_unit, rated, ambient_c)
        penalty = 0.0 if loading >= GENERATOR.min_loading_frac else 1000.0
        score = lph + penalty
        if best is None or score < best["score"]:
            best = {"score": score, "n": n, "per_unit": per_unit,
                    "loading": loading, "lph": lph}

    assert best is not None
    output = best["per_unit"] * best["n"]
    return {
        "units_running": best["n"],
        "output_kw": round(output, 3),
        "per_unit_kw": round(best["per_unit"], 3),
        "fuel_lph": round(best["lph"], 4),
        "loading_pct": round(best["loading"] * 100.0, 2),
        "efficiency": genset_efficiency(best["per_unit"], rated, ambient_c),
        "wet_stacking": best["loading"] < GENERATOR.min_loading_frac,
        "unmet_kw": round(max(0.0, required_kw - output), 3),
    }


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------


def battery_temp_derate(battery_temp_c: float) -> float:
    """Usable-capacity multiplier at a given cell temperature."""
    delta = BATTERY.temp_derate_ref_c - battery_temp_c
    if delta <= 0:
        return 1.0
    factor = 1.0 - BATTERY.temp_derate_per_c * delta
    return max(BATTERY.temp_derate_floor, factor)


def battery_room_temp(ambient_c: float) -> float:
    """The battery room is heated; it tracks ambient only weakly."""
    return max(5.0, 18.0 + 0.12 * (ambient_c + 20.0))


@dataclass
class BatteryStep:
    soc_pct: float
    stored_kwh: float
    power_kw: float  # + charging, - discharging
    accepted_kw: float
    effective_capacity_kwh: float
    derate_factor: float
    at_floor: bool
    at_ceiling: bool


def battery_step(soc_pct: float, requested_kw: float, hours: float,
                 ambient_c: float) -> BatteryStep:
    """Advance the battery one timestep.

    `requested_kw` > 0 charges, < 0 discharges. Returns what the pack was
    actually able to accept or deliver within SoC and power limits.
    """
    derate = battery_temp_derate(battery_room_temp(ambient_c))
    effective_capacity = BATTERY.nominal_capacity_kwh * BATTERY.state_of_health * derate
    stored = effective_capacity * soc_pct / 100.0

    floor = effective_capacity * BATTERY.soc_min_pct / 100.0
    ceiling = effective_capacity * BATTERY.soc_max_pct / 100.0

    eta = math.sqrt(BATTERY.round_trip_efficiency)

    if requested_kw > 0:
        power = min(requested_kw, BATTERY.max_charge_kw)
        headroom = max(0.0, ceiling - stored)
        delivered = min(power * hours * eta, headroom)
        stored += delivered
        accepted = (delivered / eta / hours) if hours > 0 else 0.0
    elif requested_kw < 0:
        power = min(-requested_kw, BATTERY.max_discharge_kw)
        available = max(0.0, stored - floor)
        drawn = min(power * hours / eta, available)
        stored -= drawn
        accepted = -((drawn * eta) / hours) if hours > 0 else 0.0
    else:
        accepted = 0.0

    # Self-discharge
    stored -= effective_capacity * (BATTERY.self_discharge_pct_per_day / 100.0) * (hours / 24.0)
    stored = max(0.0, min(stored, ceiling))
    new_soc = (stored / effective_capacity * 100.0) if effective_capacity > 0 else 0.0

    return BatteryStep(
        soc_pct=round(new_soc, 3),
        stored_kwh=round(stored, 3),
        power_kw=round(accepted, 3),
        accepted_kw=round(accepted, 3),
        effective_capacity_kwh=round(effective_capacity, 2),
        derate_factor=round(derate, 4),
        at_floor=new_soc <= BATTERY.soc_min_pct + 0.2,
        at_ceiling=new_soc >= BATTERY.soc_max_pct - 0.2,
    )


def battery_available_kwh(soc_pct: float, ambient_c: float,
                          floor_pct: float | None = None) -> float:
    """Energy deliverable above the protected floor."""
    derate = battery_temp_derate(battery_room_temp(ambient_c))
    cap = BATTERY.nominal_capacity_kwh * BATTERY.state_of_health * derate
    floor = BATTERY.soc_min_pct if floor_pct is None else floor_pct
    return max(0.0, cap * (soc_pct - floor) / 100.0)
