"""Domain schemas for the POLARIS API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import ORMModel


# ---------------------------------------------------------------------------
# Weather (REAL / LIVE)
# ---------------------------------------------------------------------------


class FieldValue(BaseModel):
    """One displayed value plus where it came from."""

    value: float | str | bool | None = None
    source: str | None = None
    provider: str | None = None
    observed_at: datetime | None = None
    age_seconds: float | None = None
    is_measured: bool = True


class CurrentWeather(BaseModel):
    station_code: str
    station_name: str
    latitude: float
    longitude: float
    status: Literal["LIVE", "STALE", "FAILED"]
    is_live: bool
    is_stale: bool
    is_fresh: bool = False
    observed_at: datetime | None = None
    fetched_at: datetime | None = None
    age_seconds: float | None = None
    primary_source: str | None = None
    primary_provider: str | None = None
    contributing_sources: list[str] = Field(default_factory=list)
    notice: str | None = None
    fields: dict[str, FieldValue] = Field(default_factory=dict)


class WeatherObservationOut(ORMModel):
    id: int
    observed_at: datetime
    temperature_c: float
    wind_speed_ms: float
    wind_direction_deg: float | None = None
    wind_gust_ms: float | None = None
    solar_radiation_wm2: float | None = None
    humidity_pct: float | None = None
    pressure_hpa: float | None = None
    cloud_cover_pct: float | None = None
    snowfall_mm: float | None = None
    wind_chill_c: float | None = None
    air_density_kg_m3: float | None = None
    is_polar_night: bool | None = None
    is_blizzard: bool | None = None
    provenance: str
    source: str
    source_provider: str
    fetched_at: datetime

    @field_validator("provenance", mode="before")
    @classmethod
    def _enum_value(cls, v):
        return getattr(v, "value", v)


class SourceStatusOut(ORMModel):
    provider_key: str
    provider_label: str
    endpoint: str | None = None
    priority: int
    is_enabled: bool
    is_active_source: bool
    health: str
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None
    last_http_status: int | None = None
    last_latency_ms: float | None = None
    consecutive_failures: int
    total_attempts: int
    total_successes: int
    total_observations: int
    supports_solar_radiation: bool
    notes: str | None = None

    @field_validator("health", mode="before")
    @classmethod
    def _enum_value(cls, v):
        return getattr(v, "value", v)


# ---------------------------------------------------------------------------
# Energy (MODELLED)
# ---------------------------------------------------------------------------


class EnergyStatusOut(BaseModel):
    timestamp: datetime
    total_load_kw: float
    critical_load_kw: float
    deferrable_load_kw: float
    shed_load_kw: float
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
    fuel_level_l: float
    fuel_level_pct: float
    fuel_consumed_l: float
    renewable_fraction: float
    co2_kg: float
    temperature_c: float
    wind_speed_ms: float


class EnergyHistoryPoint(BaseModel):
    recorded_at: datetime
    total_load_kw: float
    critical_load_kw: float
    wind_generation_kw: float
    solar_generation_kw: float
    generator_output_kw: float
    battery_soc_pct: float
    fuel_level_l: float
    renewable_fraction: float
    shed_load_kw: float


class LoadChannelOut(BaseModel):
    channel_key: str
    channel_label: str
    priority: str
    demand_kw: float
    is_deferrable: bool


# ---------------------------------------------------------------------------
# Forecast (AI)
# ---------------------------------------------------------------------------


class LoadForecastPoint(BaseModel):
    target_time: datetime
    horizon_h: int
    predicted_load_kw: float
    load_kw_p10: float | None = None
    load_kw_p90: float | None = None
    critical_load_kw: float
    deferrable_load_kw: float
    input_temperature_c: float | None = None
    input_wind_speed_ms: float | None = None
    weather_provenance: str | None = None
    model_version: str | None = None


class RenewableForecastPoint(BaseModel):
    target_time: datetime
    horizon_h: int
    wind_kw: float
    solar_kw: float
    total_kw: float
    wind_kw_p10: float | None = None
    wind_kw_p90: float | None = None
    solar_kw_p10: float | None = None
    solar_kw_p90: float | None = None
    wind_physical_kw: float | None = None
    solar_physical_kw: float | None = None
    ml_correction_kw: float | None = None
    input_wind_speed_ms: float | None = None
    input_solar_radiation_wm2: float | None = None
    icing_risk: bool | None = None
    turbine_curtailed: bool | None = None
    weather_provenance: str | None = None


# ---------------------------------------------------------------------------
# Optimization
# ---------------------------------------------------------------------------


class OptimizationRequest(BaseModel):
    horizon_h: int = Field(48, ge=6, le=168)
    allow_deferral: bool = True
    reserve_soc_pct: float | None = Field(None, ge=5, le=80)
    generators_available: int | None = Field(None, ge=0, le=3)


class DispatchHourOut(BaseModel):
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


class OptimizationOut(BaseModel):
    run_at: datetime
    horizon_h: int
    baseline_fuel_l: float
    optimized_fuel_l: float
    fuel_saved_l: float
    fuel_saved_pct: float
    renewable_fraction: float
    renewable_curtailed_kwh: float
    generator_runtime_h: float
    generator_starts: int
    load_shed_kwh: float
    load_deferred_kwh: float
    unserved_critical_kwh: float
    battery_throughput_kwh: float
    final_soc_pct: float
    co2_avoided_kg: float
    feasible: bool
    solve_ms: float
    rationale: list[str] = Field(default_factory=list)
    constraints_binding: list[str] = Field(default_factory=list)
    schedule: list[DispatchHourOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Survival
# ---------------------------------------------------------------------------


class RiskFactorOut(BaseModel):
    key: str
    label: str
    score: float
    weight: float
    weighted: float
    value: str
    detail: str


class RiskOut(BaseModel):
    level: Literal["LOW", "MODERATE", "ELEVATED", "HIGH", "SEVERE"]
    score: float
    score_pct: float
    headline: str
    color: str
    factors: list[RiskFactorOut] = Field(default_factory=list)
    top_drivers: list[str] = Field(default_factory=list)
    provenance: str = "MODELLED"


class AutonomyModeOut(BaseModel):
    hours: float | None = None
    demand_kw: float
    renewable_kw: float
    limited_by: str
    label: str
    description: str


class AutonomyModesOut(BaseModel):
    normal_hours: float | None = None
    critical_only_hours: float | None = None
    crisis_hours: float | None = None
    normal: AutonomyModeOut
    critical_only: AutonomyModeOut
    crisis: AutonomyModeOut


class SurvivalOut(BaseModel):
    estimated_autonomy_hours: float | None
    estimated_autonomy_days: float | None
    battery_hours: float | None
    fuel_hours: float | None
    limited_by: str
    modes: AutonomyModesOut | None = None
    risk: RiskOut | None = None
    available_energy_kwh: float
    battery_available_kwh: float
    fuel_available_l: float
    fuel_usable_l: float
    fuel_energy_kwh: float
    critical_demand_kw: float
    total_demand_kw: float
    renewable_generation_kw: float
    generators_available: int
    generator_capacity_kw: float
    battery_soc_pct: float
    fuel_level_pct: float
    status: Literal["SECURE", "ADEQUATE", "WARNING", "CRITICAL"]
    headroom_vs_threshold_h: float | None = None
    breakdown: list[dict[str, Any]] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Crisis
# ---------------------------------------------------------------------------


class CrisisRequest(BaseModel):
    """Scenario preset plus optional operator slider overrides.

    Any override left null keeps the preset's value, so the sliders and the
    scenario buttons compose rather than fight.
    """

    scenario: Literal[
        "NORMAL_OPERATION", "SEVERE_STORM", "LOW_SOLAR", "WIND_FAILURE",
        "GENERATOR_FAILURE", "HIGH_DEMAND", "COMBINED_CRISIS",
    ]
    duration_h: int = Field(72, ge=6, le=240)
    severity: float = Field(1.0, ge=0.1, le=2.0)

    # --- operator sliders (all optional) ---
    wind_speed_ms: float | None = Field(None, ge=0, le=60)
    solar_radiation_wm2: float | None = Field(None, ge=0, le=1400)
    temperature_c: float | None = Field(None, ge=-80, le=20)
    demand_multiplier: float | None = Field(None, ge=0.3, le=3.0)
    initial_soc_pct: float | None = Field(None, ge=5, le=100)
    initial_fuel_l: float | None = Field(None, ge=0, le=100_000)
    generators_available: int | None = Field(None, ge=0, le=3)
    turbines_available: int | None = Field(None, ge=0, le=6)


class CrisisOut(BaseModel):
    scenario: str
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
    time_to_first_shed_h: float | None = None
    severity_rating: str
    summary: str
    actions_taken: list[str] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    recommendation: dict[str, Any] = Field(default_factory=dict)
    overrides_applied: dict[str, Any] = Field(default_factory=dict)
    disclaimer: str


# ---------------------------------------------------------------------------
# Recommendations / alerts / XAI
# ---------------------------------------------------------------------------


class RecommendationOut(ORMModel):
    id: int
    generated_at: datetime
    category: str
    urgency: str
    title: str
    action: str
    rationale: str
    drivers: list[dict[str, Any]] | None = None
    reasons: list[str] | None = None
    expected_benefit: str | None = None
    estimated_fuel_saving_l: float | None = None
    estimated_autonomy_gain_h: float | None = None
    confidence: float | None = None
    counterfactual: str | None = None
    evidence: dict[str, Any] | None = None
    source_module: str | None = None
    is_active: bool

    @field_validator("category", "urgency", mode="before")
    @classmethod
    def _enum_value(cls, v):
        return getattr(v, "value", v)


class AlertOut(ORMModel):
    id: int
    raised_at: datetime
    resolved_at: datetime | None = None
    code: str
    severity: str
    status: str
    title: str
    message: str
    subsystem: str | None = None
    metric_name: str | None = None
    metric_value: float | None = None
    threshold_value: float | None = None
    recommended_action: str | None = None

    @field_validator("severity", "status", mode="before")
    @classmethod
    def _enum_value(cls, v):
        return getattr(v, "value", v)


class ExplainabilityOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    target: str
    prediction: float
    baseline: float
    timestamp: datetime | None = None
    method: str
    narrative: str
    contributions: list[dict[str, Any]] = Field(default_factory=list)
    counterfactuals: list[str] = Field(default_factory=list)
    global_importance: list[dict[str, Any]] = Field(default_factory=list)
    dispatch_explanation: dict[str, Any] | None = None
    model_reports: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class DashboardSummary(BaseModel):
    station: dict[str, Any]
    live_weather: CurrentWeather
    energy: EnergyStatusOut | None = None
    survival: SurvivalOut | None = None
    optimization: dict[str, Any] | None = None
    top_recommendation: RecommendationOut | None = None
    active_alerts: list[AlertOut] = Field(default_factory=list)
    alert_counts: dict[str, int] = Field(default_factory=dict)
    forecast_preview: list[dict[str, Any]] = Field(default_factory=list)
    data_sources: list[SourceStatusOut] = Field(default_factory=list)
    scheduler: dict[str, Any] = Field(default_factory=dict)
    pipeline: dict[str, Any] = Field(default_factory=dict)
    banners: dict[str, str] = Field(default_factory=dict)
