"""
POLARIS - Station physical configuration.

============================  DATA HONESTY  ================================
Everything in this module is a RESEARCH-BASED ENERGY MODEL.

It is *not* telemetry from NCPOR, Bharati, Maitri or any other Antarctic
research station. No public real-time electrical telemetry feed exists for
Indian (or most other) Antarctic stations.

The numbers below are engineering estimates assembled from published
literature on polar station energy systems (Princess Elisabeth Antarctica,
McMurdo, Halley VI, Neumayer III, Scott Base) and standard equipment
datasheets. They are dimensionally correct and physically consistent, and
they are used to DEMONSTRATE the POLARIS decision-support methodology.

Real data used by POLARIS  ->  weather only (see app/services/weather_ingest.py)
Modelled data              ->  everything in this file
============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# Provenance labels used across the API and the UI
# ---------------------------------------------------------------------------

DATA_BANNER = "REAL WEATHER DATA + RESEARCH-BASED ENERGY MODEL"
MODEL_DISCLAIMER = (
    "Station energy parameters are modelled/estimated for research and "
    "demonstration purposes."
)
LIVE_WEATHER_NOTE = (
    "Live weather is retrieved from Maitri (WMO 89514), an Indian Antarctic "
    "Programme station, via the WMO Global Telecommunication System."
)
SIMULATION_DISCLAIMER = "SIMULATED SCENARIO - NOT LIVE STATION TELEMETRY"
STALE_NOTICE = "Live source unavailable - displaying last successful observation"
NO_TELEMETRY_NOTE = (
    "No public real-time electrical telemetry feed exists for Maitri or any "
    "other Indian Antarctic station. Battery state of charge, fuel level, "
    "generator state and station load shown by POLARIS are RESEARCH-BASED "
    "MODEL estimates driven by the live weather inputs - not measurements."
)

REAL_FIELDS = [
    "temperature_c",
    "wind_speed_ms",
    "solar_radiation_wm2",
    "humidity_pct",
    "pressure_hpa",
    "snowfall_mm",
    "cloud_cover_pct",
]

MODELLED_FIELDS = [
    "station_load_kw",
    "battery_soc_pct",
    "battery_capacity_kwh",
    "fuel_level_l",
    "generator_state",
    "critical_load_kw",
    "energy_demand_kwh",
    "energy_autonomy_hours",
]


# ---------------------------------------------------------------------------
# Site  (real geographic coordinates - used to fetch REAL weather)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SiteConfig:
    """Real geography and identity of the site POLARIS models.

    MAITRI is an Indian Antarctic Programme station (NCPOR / IMD) in the
    Schirmacher Oasis, Dronning Maud Land. It is a WMO-reporting station
    (index 89514) whose SYNOP observations are relayed onto the Global
    Telecommunication System and are publicly retrievable in near real time.

    That makes Maitri the one Indian Antarctic station POLARIS can drive with
    genuinely live observations. Coordinates below match the NOAA/NCEI
    Integrated Surface Database entry "MAITRI, AY".
    """

    station_code: str = "MAITRI"
    station_name: str = "Maitri Research Station"
    operator: str = "Indian Antarctic Programme (NCPOR / IMD)"
    wmo_index: str = "89514"
    region: str = "Schirmacher Oasis, Dronning Maud Land"
    latitude: float = -70.7666  # REAL (NOAA ISD 89514099999)
    longitude: float = 11.75  # REAL
    elevation_m: float = 117.0  # REAL (Schirmacher Oasis)
    timezone: str = "UTC"
    # Austral polar night boundaries at 70.77 S (computed, not assumed)
    polar_night_start_doy: int = 148  # ~28 May
    polar_night_end_doy: int = 196  # ~15 Jul
    summer_crew: int = 65  # MODELLED
    winter_crew: int = 25  # MODELLED


SITE = SiteConfig()


@dataclass(frozen=True)
class ReferenceStation:
    """A real WMO-reporting Antarctic station used as reference/corroboration.

    These carry NO energy model - POLARIS only ingests their weather.
    """

    code: str
    name: str
    operator: str
    wmo_index: str | None
    icao: str | None
    latitude: float
    longitude: float
    elevation_m: float
    note: str


#: Real stations POLARIS ingests alongside Maitri. Bharati (the other Indian
#: station, in the Larsemann Hills) does NOT publish a public real-time feed;
#: its nearest WMO-reporting neighbours are listed here and are labelled as
#: such in the UI. POLARIS never presents these as Bharati's own telemetry.
REFERENCE_STATIONS: list[ReferenceStation] = [
    ReferenceStation(
        code="PROGRESS",
        name="Progress",
        operator="Russian Antarctic Expedition",
        wmo_index="89574",
        icao=None,
        latitude=-69.3833,
        longitude=76.3833,
        elevation_m=64.0,
        note="Larsemann Hills. Nearest WMO-reporting station to the Indian "
        "Bharati station (~7 km). Used as a regional reference only.",
    ),
    ReferenceStation(
        code="NZSP",
        name="Amundsen-Scott South Pole",
        operator="United States Antarctic Program",
        wmo_index="89009",
        icao="NZSP",
        latitude=-89.98,
        longitude=180.0,
        elevation_m=2830.0,
        note="Continental interior reference via NOAA Aviation Weather METAR.",
    ),
]

#: Bharati's real coordinates, kept for the map/context panel only.
BHARATI_COORDS = (-69.4064, 76.1866)


# ---------------------------------------------------------------------------
# Wind generation asset  (MODELLED)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindConfig:
    n_turbines: int = 6
    rated_kw_each: float = 10.0
    hub_height_m: float = 18.0
    anemometer_height_m: float = 10.0
    roughness_length_m: float = 0.0024  # snow / ice surface
    cut_in_ms: float = 3.0
    rated_ms: float = 12.0
    cut_out_ms: float = 25.0
    restart_ms: float = 20.0  # hysteresis after a cut-out
    availability: float = 0.95
    # Rime-ice accretion risk: high humidity + near-freezing temperatures
    icing_temp_band_c: tuple = (-9.0, 0.5)
    icing_humidity_pct: float = 85.0
    icing_derate: float = 0.45  # output multiplier while iced

    @property
    def rated_kw(self) -> float:
        return self.n_turbines * self.rated_kw_each


WIND = WindConfig()


# ---------------------------------------------------------------------------
# Solar PV asset  (MODELLED)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SolarConfig:
    rated_kwp: float = 50.0
    tilt_deg: float = 70.0  # steep tilt: low sun angle + sheds snow
    azimuth_deg: float = 0.0  # faces true north (southern hemisphere)
    temp_coeff_per_c: float = -0.0038  # -0.38 %/degC
    noct_c: float = 44.0
    system_efficiency: float = 0.84  # inverter + wiring + mismatch
    soiling_snow_loss: float = 0.06
    ground_albedo: float = 0.85  # fresh snow -> strong bifacial/ground bounce
    albedo_gain: float = 0.18  # extra irradiance fraction from snow bounce
    availability: float = 0.98


SOLAR = SolarConfig()


# ---------------------------------------------------------------------------
# Battery energy storage  (MODELLED)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatteryConfig:
    nominal_capacity_kwh: float = 600.0
    soc_min_pct: float = 15.0  # protected floor
    soc_max_pct: float = 95.0
    soc_reserve_pct: float = 30.0  # below this -> "reserve" operating regime
    max_charge_kw: float = 150.0
    max_discharge_kw: float = 200.0
    round_trip_efficiency: float = 0.92
    self_discharge_pct_per_day: float = 0.08
    # Usable capacity falls at low cell temperature. Battery room is heated,
    # so the derate is mild but not zero.
    temp_derate_ref_c: float = 20.0
    temp_derate_per_c: float = 0.0035  # 0.35 % capacity loss per degC below ref
    temp_derate_floor: float = 0.72
    state_of_health: float = 0.94  # MODELLED ageing

    @property
    def usable_kwh(self) -> float:
        span = (self.soc_max_pct - self.soc_min_pct) / 100.0
        return self.nominal_capacity_kwh * span * self.state_of_health


BATTERY = BatteryConfig()


# ---------------------------------------------------------------------------
# Diesel generation + fuel  (MODELLED)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneratorConfig:
    n_units: int = 3  # 2 duty + 1 standby
    rated_kw_each: float = 100.0
    min_loading_frac: float = 0.30  # below this -> wet-stacking damage
    optimal_loading_frac: float = 0.80
    max_loading_frac: float = 0.95
    min_run_hours: int = 2  # anti short-cycling
    start_fuel_l: float = 1.2
    start_reliability: float = 0.985
    # Willans line: fuel_lph = c0 * rated_kw + c1 * output_kw
    willans_c0: float = 0.060  # no-load loss, L/h per kW of rating
    willans_c1: float = 0.220  # marginal, L/kWh
    cold_start_penalty_below_c: float = -25.0
    cold_start_penalty_frac: float = 0.06  # +6 % fuel in deep cold

    @property
    def rated_kw(self) -> float:
        return self.n_units * self.rated_kw_each


GENERATOR = GeneratorConfig()


@dataclass(frozen=True)
class FuelConfig:
    tank_capacity_l: float = 80_000.0
    initial_level_l: float = 58_400.0
    density_kg_per_l: float = 0.82  # Antarctic special blend (ATK/SKO)
    lhv_mj_per_kg: float = 43.0
    critical_level_l: float = 12_000.0  # emergency reserve, do not cross
    low_level_l: float = 22_000.0
    resupply_interval_days: int = 365  # one ship per summer season
    heating_share_of_fuel: float = 0.0  # this model is all-electric heating

    @property
    def energy_kwh_per_l(self) -> float:
        return self.density_kg_per_l * self.lhv_mj_per_kg / 3.6


FUEL = FuelConfig()


# ---------------------------------------------------------------------------
# Load model  (MODELLED)
# ---------------------------------------------------------------------------


class LoadPriority(str, Enum):
    """Shed order is P4 -> P3 -> P2. P1 is never shed."""

    P1_LIFE_CRITICAL = "P1_LIFE_CRITICAL"
    P2_SCIENCE_CRITICAL = "P2_SCIENCE_CRITICAL"
    P3_OPERATIONAL = "P3_OPERATIONAL"
    P4_DEFERRABLE = "P4_DEFERRABLE"


PRIORITY_ORDER = [
    LoadPriority.P1_LIFE_CRITICAL,
    LoadPriority.P2_SCIENCE_CRITICAL,
    LoadPriority.P3_OPERATIONAL,
    LoadPriority.P4_DEFERRABLE,
]

SHED_ORDER = list(reversed(PRIORITY_ORDER))


@dataclass(frozen=True)
class LoadChannel:
    """One modelled electrical load group."""

    key: str
    label: str
    priority: LoadPriority
    base_kw: float  # constant floor
    diurnal_kw: float  # amplitude of daily activity cycle
    peak_hour: int  # local hour of the diurnal peak
    hdd_kw_per_c: float = 0.0  # extra kW per degC below heating balance point
    occupancy_kw_per_person: float = 0.0
    shed_fraction_max: float = 0.0  # how much of it may be shed (0..1)
    deferrable: bool = False  # may be time-shifted into a surplus window
    description: str = ""


# Heating balance point: internal gains offset outside air below this.
HEATING_BALANCE_POINT_C: float = 14.0

LOAD_CHANNELS: list[LoadChannel] = [
    # ---------------- P1 : life critical -----------------------------------
    LoadChannel(
        key="space_heating",
        label="Space Heating (electric)",
        priority=LoadPriority.P1_LIFE_CRITICAL,
        base_kw=8.0,
        diurnal_kw=1.5,
        peak_hour=7,
        hdd_kw_per_c=0.62,
        shed_fraction_max=0.15,  # setback only - never off
        description="Hydronic/electric space heating. Dominant polar load; "
        "driven by outside air temperature and wind chill.",
    ),
    LoadChannel(
        key="water_sanitation",
        label="Water & Sanitation",
        priority=LoadPriority.P1_LIFE_CRITICAL,
        base_kw=4.2,
        diurnal_kw=2.4,
        peak_hour=8,
        occupancy_kw_per_person=0.05,
        shed_fraction_max=0.10,
        description="Potable water treatment, pumping, greywater and sewage "
        "handling, pipe trace heating.",
    ),
    LoadChannel(
        key="medical_safety",
        label="Medical & Life Safety",
        priority=LoadPriority.P1_LIFE_CRITICAL,
        base_kw=3.0,
        diurnal_kw=0.5,
        peak_hour=11,
        shed_fraction_max=0.0,
        description="Medical bay, fire detection and suppression, emergency "
        "lighting, alarm and evacuation systems.",
    ),
    LoadChannel(
        key="comms_control",
        label="Emergency Comms & Control",
        priority=LoadPriority.P1_LIFE_CRITICAL,
        base_kw=5.5,
        diurnal_kw=0.8,
        peak_hour=14,
        shed_fraction_max=0.05,
        description="HF/VHF radio, satellite emergency link, SCADA, power "
        "plant control and monitoring.",
    ),
    # ---------------- P2 : science critical --------------------------------
    LoadChannel(
        key="observatory",
        label="Atmospheric & Geophysical Observatory",
        priority=LoadPriority.P2_SCIENCE_CRITICAL,
        base_kw=7.5,
        diurnal_kw=0.6,
        peak_hour=12,
        shed_fraction_max=0.25,
        description="Continuous unattended instruments: ozone, aerosol, "
        "magnetometer, seismic, GNSS. Gaps destroy time-series value.",
    ),
    LoadChannel(
        key="sample_storage",
        label="Cryogenic Sample Storage",
        priority=LoadPriority.P2_SCIENCE_CRITICAL,
        base_kw=6.0,
        diurnal_kw=0.4,
        peak_hour=15,
        shed_fraction_max=0.20,
        description="-80 degC ultra-low freezers and ice-core storage. High "
        "thermal mass tolerates short interruptions only.",
    ),
    LoadChannel(
        key="data_uplink",
        label="Data Acquisition & Satellite Uplink",
        priority=LoadPriority.P2_SCIENCE_CRITICAL,
        base_kw=4.5,
        diurnal_kw=1.8,
        peak_hour=13,
        shed_fraction_max=0.40,
        description="Server room, data archive and scheduled satellite "
        "downlink windows.",
    ),
    # ---------------- P3 : operational -------------------------------------
    LoadChannel(
        key="galley",
        label="Galley & Food Storage",
        priority=LoadPriority.P3_OPERATIONAL,
        base_kw=5.0,
        diurnal_kw=6.5,
        peak_hour=12,
        occupancy_kw_per_person=0.14,
        shed_fraction_max=0.35,
        description="Cooking, refrigeration and dry/frozen food stores.",
    ),
    LoadChannel(
        key="lighting_accom",
        label="Lighting & Accommodation",
        priority=LoadPriority.P3_OPERATIONAL,
        base_kw=4.0,
        diurnal_kw=5.0,
        peak_hour=19,
        occupancy_kw_per_person=0.11,
        shed_fraction_max=0.45,
        description="Interior/exterior lighting, accommodation sockets, "
        "ventilation. Strongly seasonal (polar night).",
    ),
    LoadChannel(
        key="workshop",
        label="Workshop & Maintenance",
        priority=LoadPriority.P3_OPERATIONAL,
        base_kw=2.0,
        diurnal_kw=6.0,
        peak_hour=10,
        occupancy_kw_per_person=0.07,
        shed_fraction_max=0.70,
        description="Mechanical workshop, welding, compressors, tools.",
    ),
    # ---------------- P4 : deferrable --------------------------------------
    LoadChannel(
        key="snow_melter",
        label="Snow Melter / Water Production",
        priority=LoadPriority.P4_DEFERRABLE,
        base_kw=2.0,
        diurnal_kw=10.0,
        peak_hour=9,
        shed_fraction_max=1.0,
        deferrable=True,
        description="Largest genuinely schedulable load. Melt water is "
        "buffered in tanks, so it can be shifted into renewable surplus.",
    ),
    LoadChannel(
        key="vehicle_charging",
        label="Vehicle & Equipment Charging",
        priority=LoadPriority.P4_DEFERRABLE,
        base_kw=1.0,
        diurnal_kw=7.0,
        peak_hour=20,
        shed_fraction_max=1.0,
        deferrable=True,
        description="Skidoo and tracked-vehicle battery charging, engine "
        "block heaters. Fully time-shiftable overnight.",
    ),
    LoadChannel(
        key="lab_nonurgent",
        label="Non-urgent Laboratory Work",
        priority=LoadPriority.P4_DEFERRABLE,
        base_kw=1.5,
        diurnal_kw=5.5,
        peak_hour=15,
        occupancy_kw_per_person=0.06,
        shed_fraction_max=1.0,
        deferrable=True,
        description="Batch sample processing, centrifuges, ovens, drying "
        "cabinets. Schedulable within a 24 h window.",
    ),
    LoadChannel(
        key="recreation",
        label="Recreation & Comfort",
        priority=LoadPriority.P4_DEFERRABLE,
        base_kw=1.2,
        diurnal_kw=3.8,
        peak_hour=21,
        occupancy_kw_per_person=0.05,
        shed_fraction_max=1.0,
        deferrable=True,
        description="Gym, media room, sauna, personal devices. First to be "
        "shed and last to be restored.",
    ),
]

LOAD_BY_KEY = {c.key: c for c in LOAD_CHANNELS}

PRIORITY_LABELS = {
    LoadPriority.P1_LIFE_CRITICAL: "P1 - Life Critical",
    LoadPriority.P2_SCIENCE_CRITICAL: "P2 - Science Critical",
    LoadPriority.P3_OPERATIONAL: "P3 - Operational",
    LoadPriority.P4_DEFERRABLE: "P4 - Deferrable",
}

PRIORITY_COLORS = {
    LoadPriority.P1_LIFE_CRITICAL: "#0B4F3F",
    LoadPriority.P2_SCIENCE_CRITICAL: "#1264A3",
    LoadPriority.P3_OPERATIONAL: "#2E9E7E",
    LoadPriority.P4_DEFERRABLE: "#7FC8D8",
}


# ---------------------------------------------------------------------------
# Operating thresholds used by the alert + recommendation engines (MODELLED)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Thresholds:
    autonomy_critical_h: float = 24.0
    autonomy_warning_h: float = 72.0
    soc_critical_pct: float = 20.0
    soc_warning_pct: float = 35.0
    fuel_critical_days: float = 20.0
    fuel_warning_days: float = 45.0
    # Antarctic Treaty / operational weather limits
    storm_wind_ms: float = 25.0
    blizzard_wind_ms: float = 17.0
    extreme_cold_c: float = -35.0
    renewable_fraction_target: float = 0.45


THRESHOLDS = Thresholds()


@dataclass(frozen=True)
class StationSummary:
    """Flat, serialisable view of the whole modelled plant."""

    site: SiteConfig = field(default_factory=lambda: SITE)
    wind: WindConfig = field(default_factory=lambda: WIND)
    solar: SolarConfig = field(default_factory=lambda: SOLAR)
    battery: BatteryConfig = field(default_factory=lambda: BATTERY)
    generator: GeneratorConfig = field(default_factory=lambda: GENERATOR)
    fuel: FuelConfig = field(default_factory=lambda: FUEL)


def station_nameplate() -> dict:
    """Plant nameplate used by the /api/dashboard and Data & Model pages."""
    return {
        "station_code": SITE.station_code,
        "station_name": SITE.station_name,
        "operator": SITE.operator,
        "wmo_index": SITE.wmo_index,
        "region": SITE.region,
        "latitude": SITE.latitude,
        "longitude": SITE.longitude,
        "elevation_m": SITE.elevation_m,
        "wind_rated_kw": WIND.rated_kw,
        "wind_turbines": WIND.n_turbines,
        "solar_rated_kwp": SOLAR.rated_kwp,
        "battery_nominal_kwh": BATTERY.nominal_capacity_kwh,
        "battery_usable_kwh": round(BATTERY.usable_kwh, 1),
        "generator_rated_kw": GENERATOR.rated_kw,
        "generator_units": GENERATOR.n_units,
        "fuel_capacity_l": FUEL.tank_capacity_l,
        "fuel_energy_kwh_per_l": round(FUEL.energy_kwh_per_l, 2),
        "summer_crew": SITE.summer_crew,
        "winter_crew": SITE.winter_crew,
        "data_banner": DATA_BANNER,
        "model_disclaimer": MODEL_DISCLAIMER,
        "live_weather_note": LIVE_WEATHER_NOTE,
        "no_telemetry_note": NO_TELEMETRY_NOTE,
        "real_fields": REAL_FIELDS,
        "modelled_fields": MODELLED_FIELDS,
    }
