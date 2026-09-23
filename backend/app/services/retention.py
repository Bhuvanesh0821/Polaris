"""Bounded storage: delete rows no part of POLARIS reads any more.

The hosted database (Neon free tier) has a 0.5 GB ceiling, and without this
every refresh cycle would add run logs, modelled hours and recommendation
history forever. Each window below is wider than the longest read of that
table, so pruning never removes data an endpoint or model can still use:

- weather       180 d  (models train on settings.history_days = 120 d)
- modelled state 45 d  (/energy/history reads at most 30 d)
- run logs       30 d  (only the latest rows are ever read)

Active and acknowledged alerts are never pruned, whatever their age.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Alert,
    AlertStatus,
    BatteryState,
    CrisisSimulation,
    EnergyParameter,
    FuelState,
    GeneratorState,
    IngestionRun,
    LoadProfile,
    OptimizationResult,
    Recommendation,
    WeatherObservation,
)

log = logging.getLogger("polaris.retention")

WEATHER_DAYS = max(180, settings.history_days + 30)
MODELLED_STATE_DAYS = 45
RUN_LOG_DAYS = 30


def prune_old_data(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Delete expired rows and commit. Returns rows removed per table."""
    now = now or datetime.now(timezone.utc)
    weather_cut = now - timedelta(days=WEATHER_DAYS)
    state_cut = now - timedelta(days=MODELLED_STATE_DAYS)
    log_cut = now - timedelta(days=RUN_LOG_DAYS)

    rules = [
        (WeatherObservation, WeatherObservation.observed_at < weather_cut),
        *[(m, m.recorded_at < state_cut) for m in (
            EnergyParameter, BatteryState, GeneratorState, FuelState, LoadProfile)],
        (IngestionRun, IngestionRun.started_at < log_cut),
        (OptimizationResult, OptimizationResult.run_at < log_cut),
        (CrisisSimulation, CrisisSimulation.run_at < log_cut),
        (Recommendation, (Recommendation.generated_at < log_cut)
                         & Recommendation.is_active.is_(False)),
        (Alert, (Alert.raised_at < log_cut) & (Alert.status == AlertStatus.RESOLVED)),
    ]

    removed: dict[str, int] = {}
    for model, condition in rules:
        n = db.execute(delete(model).where(condition)).rowcount or 0
        if n:
            removed[model.__tablename__] = n
    db.commit()
    if removed:
        log.info("Retention pruned %s", removed)
    return removed
