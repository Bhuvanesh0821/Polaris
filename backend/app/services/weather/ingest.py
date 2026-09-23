"""Live weather ingestion orchestrator.

Responsibilities
----------------
1. Run the provider chain and record per-provider health in PostgreSQL.
2. Persist every genuine observation to `weather_observations` (upsert on
   station + observed_at, preferring the authoritative source).
3. Build the `CurrentConditions` view the dashboard reads, with per-field
   source attribution.
4. Report staleness honestly when every provider fails - never fabricate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.core.station import SITE, STALE_NOTICE
from app.models import (
    DataProvenance,
    DataSourceStatus,
    IngestionRun,
    SourceHealth,
    Station,
    WeatherObservation,
)
from app.core.physics import air_density, wind_chill_c
from app.services.weather.providers import (
    PROVIDER_DESCRIPTIONS,
    ProviderOutcome,
    WeatherProvider,
    WeatherReading,
    default_providers,
)

log = logging.getLogger("polaris.ingest")

# --- Freshness thresholds -------------------------------------------------
# The authoritative source is Maitri, which transmits at the synoptic hours
# 00/06/12/18 UTC. An observation is therefore routinely up to six hours old
# during entirely normal operation, and calling that "stale" would cry wolf
# on every single cycle. POLARIS only declares STALE once a full reporting
# cycle has definitively been missed.

#: One synoptic reporting cycle.
SYNOPTIC_CYCLE_S = 6 * 3600
#: Within half a cycle - the observation is as fresh as the source can be.
FRESH_WITHIN_S = 3 * 3600
#: Beyond 1.5 cycles a scheduled report has certainly been missed.
STALE_AFTER_S = 9 * 3600


# ---------------------------------------------------------------------------
# Views returned to the API layer
# ---------------------------------------------------------------------------


@dataclass
class FieldAttribution:
    """Which provider supplied one displayed value, and when."""

    value: float | str | None
    source: str | None
    provider: str | None
    observed_at: datetime | None
    age_seconds: float | None
    is_measured: bool = True


@dataclass
class CurrentConditions:
    """Merged live picture. Each field states where it came from."""

    station_code: str
    observed_at: datetime | None
    fetched_at: datetime | None
    status: str  # LIVE | STALE | FAILED
    is_live: bool
    is_stale: bool
    age_seconds: float | None
    primary_source: str | None
    primary_provider: str | None
    #: True when the reading is newer than half a synoptic cycle.
    is_fresh: bool = False
    notice: str | None = None
    fields: dict[str, FieldAttribution] = field(default_factory=dict)
    contributing_sources: list[str] = field(default_factory=list)

    def value(self, name: str):
        f = self.fields.get(name)
        return f.value if f else None


@dataclass
class IngestReport:
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    trigger: str
    outcomes: list[ProviderOutcome]
    written: int
    skipped: int
    any_success: bool
    active_provider: str | None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": round(self.duration_ms, 1),
            "trigger": self.trigger,
            "written": self.written,
            "skipped": self.skipped,
            "any_success": self.any_success,
            "active_provider": self.active_provider,
            "error": self.error,
            "providers": [
                {
                    "key": o.provider_key,
                    "label": o.provider_label,
                    "ok": o.ok,
                    "readings": len(o.readings),
                    "usable": len(o.usable_readings),
                    "http_status": o.http_status,
                    "latency_ms": o.latency_ms,
                    "attempts": o.attempts,
                    "error": o.error,
                }
                for o in self.outcomes
            ],
        }


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

#: Ranking used when two providers report the same hour. Lower wins.
PROVIDER_RANK = {"ogimet_synop": 0, "open_meteo": 1, "met_norway": 2,
                 "noaa_metar": 3, "open_meteo_archive": 4}


def _enrich(reading: WeatherReading) -> dict[str, Any]:
    """Add POLARIS-derived physics to a raw reading."""
    t, w = reading.temperature_c, reading.wind_speed_ms
    return {
        "wind_chill_c": wind_chill_c(t, w) if t is not None and w is not None else None,
        "air_density_kg_m3": air_density(t, reading.pressure_hpa) if t is not None else None,
    }


def persist_readings(
    db: Session, station_id: int, readings: list[WeatherReading], fetched_at: datetime
) -> tuple[int, int]:
    """Upsert observations. Returns (written, skipped).

    On conflict the row is replaced only when the incoming provider ranks at
    least as authoritative as the stored one, so a coarse fallback never
    overwrites a real station observation.
    """
    written = skipped = 0
    for r in readings:
        if not r.is_usable:
            skipped += 1
            continue
        derived = _enrich(r)
        values = {
            "station_id": station_id,
            "observed_at": r.observed_at,
            "temperature_c": r.temperature_c,
            "apparent_temperature_c": r.apparent_temperature_c,
            "wind_speed_ms": r.wind_speed_ms,
            "wind_gust_ms": r.wind_gust_ms,
            "wind_direction_deg": r.wind_direction_deg,
            "solar_radiation_wm2": r.solar_radiation_wm2,
            "direct_radiation_wm2": r.direct_radiation_wm2,
            "diffuse_radiation_wm2": r.diffuse_radiation_wm2,
            "humidity_pct": r.humidity_pct,
            "pressure_hpa": r.pressure_hpa,
            "cloud_cover_pct": r.cloud_cover_pct,
            "snowfall_mm": r.snowfall_mm,
            "wind_chill_c": derived["wind_chill_c"],
            "air_density_kg_m3": derived["air_density_kg_m3"],
            "is_polar_night": _is_polar_night(r.observed_at),
            "is_blizzard": r.is_blizzard,
            "provenance": r.provenance,
            "source": r.source,
            "source_provider": r.source_provider,
            "source_station_code": r.source_station_code,
            "fetched_at": fetched_at,
            "raw_payload": r.raw_payload,
        }
        stmt = pg_insert(WeatherObservation).values(**values)

        incoming_rank = PROVIDER_RANK.get(r.source_provider, 99)
        # Preserve a better-ranked existing row; otherwise take the new one.
        update_cols = {
            k: stmt.excluded[k]
            for k in values
            if k not in ("station_id", "observed_at")
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_weather_station_ts",
            set_=update_cols,
            where=_rank_expr(incoming_rank),
        )
        db.execute(stmt)
        written += 1
    db.flush()
    return written, skipped


def _rank_expr(incoming_rank: int):
    """SQL predicate: only overwrite when the new provider is >= as good."""
    from sqlalchemy import case, literal

    ranks = case(
        {k: literal(v) for k, v in PROVIDER_RANK.items()},
        value=WeatherObservation.source_provider,
        else_=literal(99),
    )
    return ranks >= incoming_rank


def _is_polar_night(ts: datetime) -> bool:
    doy = ts.timetuple().tm_yday
    return SITE.polar_night_start_doy <= doy <= SITE.polar_night_end_doy


def update_source_status(db: Session, provider: WeatherProvider,
                         outcome: ProviderOutcome, is_active: bool) -> None:
    """Record this attempt against the provider's rolling health row."""
    now = datetime.now(timezone.utc)
    row = db.scalar(
        select(DataSourceStatus).where(
            DataSourceStatus.provider_key == outcome.provider_key
        )
    )
    if row is None:
        row = DataSourceStatus(
            provider_key=outcome.provider_key,
            provider_label=outcome.provider_label,
            priority=provider.priority,
            freshness_window_s=provider.freshness_window_s,
            supports_solar_radiation=provider.supports_solar_radiation,
            notes=PROVIDER_DESCRIPTIONS.get(outcome.provider_key, {}).get("kind"),
        )
        db.add(row)

    row.provider_label = outcome.provider_label
    row.endpoint = outcome.endpoint or row.endpoint
    row.last_attempt_at = now
    row.last_latency_ms = outcome.latency_ms
    row.last_http_status = outcome.http_status
    row.total_attempts = (row.total_attempts or 0) + 1
    row.is_active_source = is_active

    if outcome.ok:
        row.last_success_at = now
        row.consecutive_failures = 0
        row.total_successes = (row.total_successes or 0) + 1
        row.total_observations = (row.total_observations or 0) + len(
            outcome.usable_readings
        )
        row.last_error = None
        # Health describes THIS provider's own fetch. A healthy secondary is
        # not "degraded" just because a higher-priority source also answered -
        # `is_active_source` already records which one is driving the model.
        row.health = SourceHealth.OK
    else:
        row.last_failure_at = now
        row.consecutive_failures = (row.consecutive_failures or 0) + 1
        row.last_error = outcome.error
        row.health = SourceHealth.FAILED
    db.flush()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def get_primary_station(db: Session) -> Station:
    station = db.scalar(select(Station).where(Station.code == SITE.station_code))
    if station is None:
        raise RuntimeError(
            f"Station {SITE.station_code} is not provisioned. "
            "Run: python -m scripts.init_db"
        )
    return station


async def run_ingestion(db: Session, trigger: str = "scheduled",
                        providers: list[WeatherProvider] | None = None) -> IngestReport:
    """Execute one full refresh cycle across the provider chain."""
    started = datetime.now(timezone.utc)
    station = get_primary_station(db)

    if not settings.allow_network_ingest:
        # Honest no-op: make no outbound calls, and do not invent readings.
        msg = (
            "Network ingestion is disabled (ALLOW_NETWORK_INGEST=false). "
            "No live weather was fetched; POLARIS will keep serving the last "
            "genuine observation, flagged stale."
        )
        log.warning(msg)
        finished = datetime.now(timezone.utc)
        db.add(IngestionRun(
            started_at=started, finished_at=finished,
            duration_ms=(finished - started).total_seconds() * 1000,
            trigger=trigger, provider_key=None, succeeded=False,
            observations_written=0, observations_skipped=0, attempts=0,
            error=msg,
        ))
        db.commit()
        return IngestReport(
            started_at=started, finished_at=finished,
            duration_ms=(finished - started).total_seconds() * 1000,
            trigger=trigger, outcomes=[], written=0, skipped=0,
            any_success=False, active_provider=None, error=msg,
        )

    chain = providers or default_providers()
    outcomes: list[ProviderOutcome] = []
    written = skipped = 0
    active_provider: str | None = None

    for provider in chain:
        outcome = await provider.fetch()
        outcomes.append(outcome)
        if outcome.ok and outcome.usable_readings:
            w, s = persist_readings(
                db, station.id, outcome.usable_readings, fetched_at=started
            )
            written += w
            skipped += s
            # First successful provider in priority order is the active one.
            if active_provider is None:
                active_provider = outcome.provider_key
        update_source_status(
            db, provider, outcome, is_active=(outcome.provider_key == active_provider)
        )

    finished = datetime.now(timezone.utc)
    duration = (finished - started).total_seconds() * 1000
    any_success = active_provider is not None

    report = IngestReport(
        started_at=started, finished_at=finished, duration_ms=duration,
        trigger=trigger, outcomes=outcomes, written=written, skipped=skipped,
        any_success=any_success, active_provider=active_provider,
        error=None if any_success else "; ".join(
            f"{o.provider_key}: {o.error}" for o in outcomes if o.error
        ) or "All providers failed",
    )

    db.add(IngestionRun(
        started_at=started, finished_at=finished, duration_ms=duration,
        trigger=trigger, provider_key=active_provider, succeeded=any_success,
        observations_written=written, observations_skipped=skipped,
        attempts=sum(o.attempts for o in outcomes),
        error=report.error, detail=report.as_dict(),
    ))
    db.commit()

    if any_success:
        log.info("Ingest OK via %s: %d observations", active_provider, written)
    else:
        log.error("Ingest FAILED on every provider: %s", report.error)
    return report


# ---------------------------------------------------------------------------
# Current-conditions view
# ---------------------------------------------------------------------------

_MERGE_FIELDS = [
    "temperature_c", "wind_speed_ms", "wind_direction_deg", "wind_gust_ms",
    "humidity_pct", "solar_radiation_wm2", "pressure_hpa", "cloud_cover_pct",
    "snowfall_mm", "apparent_temperature_c", "wind_chill_c",
    "air_density_kg_m3",
]


def build_current_conditions(db: Session, station_id: int,
                             now: datetime | None = None) -> CurrentConditions:
    """Merge the most recent real observations into one attributed view.

    The authoritative Maitri SYNOP wins for every field it actually reports;
    fields it cannot report (solar radiation, often humidity) fall through to
    the next source and are labelled with that source. Nothing is invented.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    rows = list(db.scalars(
        select(WeatherObservation)
        .where(
            WeatherObservation.station_id == station_id,
            WeatherObservation.observed_at <= now + timedelta(minutes=5),
            WeatherObservation.observed_at >= cutoff,
            WeatherObservation.provenance.in_(
                [DataProvenance.LIVE_OBSERVED, DataProvenance.REAL_OBSERVED]
            ),
        )
        .order_by(WeatherObservation.observed_at.desc())
        .limit(200)
    ))

    if not rows:
        # The newest row overall is usually a FORECAST hour days ahead; it
        # must never stand in for "the last observation".
        last = db.scalar(
            select(WeatherObservation)
            .where(
                WeatherObservation.station_id == station_id,
                WeatherObservation.observed_at <= now,
                WeatherObservation.provenance.in_(
                    [DataProvenance.LIVE_OBSERVED, DataProvenance.REAL_OBSERVED]
                ),
            )
            .order_by(WeatherObservation.observed_at.desc())
            .limit(1)
        )
        if last is None:
            return CurrentConditions(
                station_code=SITE.station_code, observed_at=None, fetched_at=None,
                status="FAILED", is_live=False, is_stale=True, age_seconds=None,
                primary_source=None, primary_provider=None,
                notice="No observation has ever been ingested. "
                       "Run a refresh once the backend can reach a provider.",
            )
        age = (now - last.observed_at).total_seconds()
        return _single_row_view(last, now, age, status="STALE", notice=STALE_NOTICE)

    # Rank candidates: authoritative provider first, then freshness.
    def sort_key(r: WeatherObservation):
        return (PROVIDER_RANK.get(r.source_provider, 99),
                -(r.observed_at.timestamp()))

    ranked = sorted(rows, key=sort_key)
    primary = ranked[0]
    age = (now - primary.observed_at).total_seconds()

    if age <= STALE_AFTER_S:
        status, notice = "LIVE", None
    else:
        status, notice = "STALE", STALE_NOTICE

    cc = CurrentConditions(
        station_code=SITE.station_code,
        observed_at=primary.observed_at,
        fetched_at=primary.fetched_at,
        status=status,
        is_live=(status == "LIVE"),
        is_stale=(status != "LIVE"),
        is_fresh=(age <= FRESH_WITHIN_S),
        age_seconds=round(age, 1),
        primary_source=primary.source,
        primary_provider=primary.source_provider,
        notice=notice,
    )

    # Fill each field from the best-ranked row that actually reports it.
    contributing: list[str] = []
    for fname in _MERGE_FIELDS:
        chosen = None
        for r in ranked:
            v = getattr(r, fname, None)
            if v is not None:
                chosen = (r, v)
                break
        if chosen is None:
            cc.fields[fname] = FieldAttribution(
                value=None, source=None, provider=None,
                observed_at=None, age_seconds=None, is_measured=False,
            )
            continue
        row, val = chosen
        f_age = (now - row.observed_at).total_seconds()
        derived = fname in ("wind_chill_c", "air_density_kg_m3")
        cc.fields[fname] = FieldAttribution(
            value=round(val, 3) if isinstance(val, (int, float)) else val,
            source=("POLARIS derived from " + row.source) if derived else row.source,
            provider=row.source_provider,
            observed_at=row.observed_at,
            age_seconds=round(f_age, 1),
            is_measured=not derived,
        )
        if row.source not in contributing:
            contributing.append(row.source)

    # Non-numeric extras
    weather_row = next((r for r in ranked if r.is_blizzard is not None), primary)
    cc.fields["is_blizzard"] = FieldAttribution(
        value=bool(weather_row.is_blizzard), source=weather_row.source,
        provider=weather_row.source_provider,
        observed_at=weather_row.observed_at,
        age_seconds=round((now - weather_row.observed_at).total_seconds(), 1),
    )
    cc.contributing_sources = contributing
    return cc


def _single_row_view(row: WeatherObservation, now: datetime, age: float,
                     status: str, notice: str | None) -> CurrentConditions:
    cc = CurrentConditions(
        station_code=SITE.station_code, observed_at=row.observed_at,
        fetched_at=row.fetched_at, status=status, is_live=False, is_stale=True,
        age_seconds=round(age, 1), primary_source=row.source,
        primary_provider=row.source_provider, notice=notice,
        contributing_sources=[row.source],
    )
    for fname in _MERGE_FIELDS:
        v = getattr(row, fname, None)
        cc.fields[fname] = FieldAttribution(
            value=v, source=row.source if v is not None else None,
            provider=row.source_provider if v is not None else None,
            observed_at=row.observed_at if v is not None else None,
            age_seconds=round(age, 1) if v is not None else None,
            is_measured=v is not None,
        )
    return cc


def source_status_rows(db: Session) -> list[DataSourceStatus]:
    return list(db.scalars(
        select(DataSourceStatus).order_by(DataSourceStatus.priority)
    ))
