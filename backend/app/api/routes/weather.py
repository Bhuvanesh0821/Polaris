"""Live weather endpoints - the REAL-DATA surface of the API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_weather_payload, get_station_or_404
from app.core.station import REFERENCE_STATIONS
from app.database.session import get_db
from app.models import DataProvenance, Station, WeatherObservation
from app.schemas.common import PROVENANCE_REAL, Envelope
from app.schemas.polaris import CurrentWeather, SourceStatusOut, WeatherObservationOut
from app.services.weather.ingest import source_status_rows
from app.services.weather.providers import PROVIDER_DESCRIPTIONS

router = APIRouter(prefix="/weather", tags=["weather (REAL LIVE DATA)"])


@router.get("", response_model=Envelope[list[WeatherObservationOut]])
def list_weather(
    hours: int = Query(48, ge=1, le=24 * 30),
    include_forecast: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Recent REAL weather observations for the station."""
    station = get_station_or_404(db)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    provenances = [DataProvenance.LIVE_OBSERVED, DataProvenance.REAL_OBSERVED]
    if include_forecast:
        provenances.append(DataProvenance.REAL_FORECAST)

    rows = list(db.scalars(
        select(WeatherObservation)
        .where(
            WeatherObservation.station_id == station.id,
            WeatherObservation.observed_at >= since,
            WeatherObservation.provenance.in_(provenances),
        )
        .order_by(WeatherObservation.observed_at)
    ))
    return Envelope(
        data=[WeatherObservationOut.model_validate(r) for r in rows],
        provenance=PROVENANCE_REAL,
        generated_at=datetime.now(timezone.utc),
        notice=None if rows else "No observations ingested yet.",
    )


@router.get("/current", response_model=Envelope[CurrentWeather])
def current_weather(db: Session = Depends(get_db)):
    """Merged live conditions with per-field source attribution.

    If every provider is failing this still returns the last genuine
    observation, flagged STALE with an explicit notice - it never fabricates.
    """
    station = get_station_or_404(db)
    payload = current_weather_payload(db, station)
    return Envelope(
        data=payload,
        provenance=PROVENANCE_REAL,
        generated_at=datetime.now(timezone.utc),
        notice=payload.notice,
    )


@router.post("/refresh")
async def manual_refresh(run_ai: bool = Query(True), db: Session = Depends(get_db)):
    """Manual Refresh button. Pulls every provider now and reruns the AI chain."""
    from app.services.scheduler import refresh_once

    get_station_or_404(db)
    report = await refresh_once(trigger="manual", run_ai=run_ai)
    if not report.get("ok"):
        # 200 with an explicit failure body - the UI needs to show the error
        # state rather than swallow it.
        return {
            "ok": False,
            "message": "Live refresh failed. Showing last successful observation.",
            **report,
        }
    return {"ok": True, "message": "Live data refreshed.", **report}


@router.get("/sources", response_model=list[SourceStatusOut])
def data_sources(db: Session = Depends(get_db)):
    """Health of every configured live provider."""
    rows = source_status_rows(db)
    return [SourceStatusOut.model_validate(r) for r in rows]


@router.get("/providers")
def provider_catalogue():
    """Static description of what each provider can and cannot supply."""
    return {
        "providers": PROVIDER_DESCRIPTIONS,
        "reference_stations": [
            {
                "code": s.code, "name": s.name, "operator": s.operator,
                "wmo_index": s.wmo_index, "icao": s.icao,
                "latitude": s.latitude, "longitude": s.longitude,
                "elevation_m": s.elevation_m, "note": s.note,
            }
            for s in REFERENCE_STATIONS
        ],
        "note": (
            "Bharati, the other Indian Antarctic station, does not publish a "
            "public real-time feed. Its nearest WMO-reporting neighbours are "
            "listed as regional reference only and are never presented as "
            "Bharati's own data."
        ),
    }


@router.get("/stations")
def stations(db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Station).order_by(Station.id)))
    return [
        {
            "id": s.id, "code": s.code, "name": s.name, "operator": s.operator,
            "kind": s.kind.value if hasattr(s.kind, "value") else s.kind,
            "latitude": s.latitude, "longitude": s.longitude,
            "elevation_m": s.elevation_m,
            "source_station_code": s.source_station_code,
            "is_active": s.is_active, "notes": s.notes,
        }
        for s in rows
    ]
