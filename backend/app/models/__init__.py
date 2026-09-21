"""POLARIS ORM models.

Importing this package registers every mapper against Base.metadata, which is
what database.session.create_all() relies on.

Table inventory
---------------
REAL / LIVE       weather_observations, data_source_status, ingestion_runs
MODELLED          energy_parameters, load_profiles, battery_states,
                  generator_states, fuel_states
AI FORECAST       renewable_forecasts, energy_forecasts
DECISION          optimization_results, recommendations, alerts
SIMULATED         crisis_simulations
"""

from app.models.analysis import (
    Alert,
    CrisisSimulation,
    OptimizationResult,
    Recommendation,
)
from app.models.base import (
    AlertSeverity,
    AlertStatus,
    BatteryMode,
    DataProvenance,
    GeneratorStatus,
    RecommendationCategory,
    ScenarioType,
    SourceHealth,
    StationKind,
    Urgency,
)
from app.models.data_source import DataSourceStatus, IngestionRun
from app.models.energy import (
    BatteryState,
    EnergyParameter,
    FuelState,
    GeneratorState,
    LoadProfile,
)
from app.models.forecast import EnergyForecast, RenewableForecast
from app.models.station import Station
from app.models.weather import WeatherObservation

__all__ = [
    # entities
    "Station",
    "WeatherObservation",
    "DataSourceStatus",
    "IngestionRun",
    "EnergyParameter",
    "LoadProfile",
    "BatteryState",
    "GeneratorState",
    "FuelState",
    "RenewableForecast",
    "EnergyForecast",
    "OptimizationResult",
    "CrisisSimulation",
    "Recommendation",
    "Alert",
    # enums
    "DataProvenance",
    "StationKind",
    "SourceHealth",
    "BatteryMode",
    "GeneratorStatus",
    "AlertSeverity",
    "AlertStatus",
    "ScenarioType",
    "RecommendationCategory",
    "Urgency",
]
