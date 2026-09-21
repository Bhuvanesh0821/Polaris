"""Bootstrap POLARIS with REAL data and run the full pipeline once.

    python -m scripts.bootstrap                 # backfill 120 days + live + AI
    python -m scripts.bootstrap --days 60
    python -m scripts.bootstrap --no-backfill   # live refresh only

What this does NOT do: invent data. Every weather row written here comes from
a real provider response. The backfill uses ERA5 reanalysis (real historical
observations) so the ML models have something genuine to learn from; the live
refresh then pulls current conditions from the Indian Maitri station.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.session import SessionLocal  # noqa: E402
from app.services.pipeline import load_weather_frame, run_pipeline  # noqa: E402
from app.services.weather.ingest import (  # noqa: E402
    get_primary_station,
    persist_readings,
    run_ingestion,
    update_source_status,
)
from app.services.weather.providers import (  # noqa: E402
    OpenMeteoArchiveProvider,
)


def banner(text: str) -> None:
    print()
    print("=" * 68)
    print(text)
    print("=" * 68)


async def backfill(db, days: int) -> int:
    """Pull real ERA5 reanalysis history so the models have training data."""
    end = datetime.now(timezone.utc).date() - timedelta(days=6)  # ERA5 lag
    start = end - timedelta(days=days)
    provider = OpenMeteoArchiveProvider(
        start_date=start.isoformat(), end_date=end.isoformat()
    )
    print(f"  fetching REAL ERA5 reanalysis {start} -> {end} ...")
    outcome = await provider.fetch()
    if not outcome.ok:
        print(f"  FAILED: {outcome.error}")
        print("  POLARIS will not substitute synthetic data. "
              "Training may be skipped until enough live data accumulates.")
        return 0

    station = get_primary_station(db)
    written, skipped = persist_readings(
        db, station.id, outcome.usable_readings,
        fetched_at=datetime.now(timezone.utc),
    )
    update_source_status(db, provider, outcome, is_active=False)
    db.commit()
    print(f"  wrote {written} real observations ({skipped} skipped)")
    return written


async def main_async(args) -> int:
    db = SessionLocal()
    try:
        banner("1. REAL historical weather (ERA5 reanalysis)")
        if args.no_backfill:
            print("  skipped (--no-backfill)")
        else:
            await backfill(db, args.days)

        banner("2. LIVE weather from the provider chain")
        report = await run_ingestion(db, trigger="bootstrap")
        for p in report.as_dict()["providers"]:
            mark = "OK  " if p["ok"] else "FAIL"
            detail = (f"{p['usable']:>4} usable, {p['latency_ms']:>7.0f} ms"
                      if p["ok"] else f"{p['error']}")
            print(f"  [{mark}] {p['label'][:52]:<52} {detail}")
        if not report.any_success:
            print("\n  No provider succeeded. POLARIS will not fabricate weather.")
            print("  Check network access and retry.")
            return 1
        print(f"\n  active source: {report.active_provider}")
        print(f"  observations written: {report.written}")

        banner("3. AI pipeline (train -> forecast -> optimise -> recommend)")
        frame = load_weather_frame(db, get_primary_station(db).id, days=200)
        print(f"  real observations available: {len(frame)}")
        result = run_pipeline(db, trigger="bootstrap", retrain=True)
        for stage, info in result.stages.items():
            if isinstance(info, dict):
                ok = info.get("ok", info.get("is_trained", True))
                print(f"  {stage:<18} {'OK' if ok else 'SKIPPED'}")
        if result.error:
            print(f"\n  pipeline error: {result.error}")
            return 1
        print(f"\n  forecast rows: {result.forecast_rows}")
        print(f"  recommendations: {result.recommendations}")
        print(f"  alerts: {result.alerts}")
        print(f"  duration: {result.duration_ms:.0f} ms")

        banner("POLARIS is ready")
        print("  Start the API:  uvicorn app.main:app --reload --port 8000")
        print("  Then open:      http://localhost:8000/docs")
        return 0
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap POLARIS with real data")
    parser.add_argument("--days", type=int, default=120,
                        help="days of ERA5 history to backfill (default 120)")
    parser.add_argument("--no-backfill", action="store_true",
                        help="skip the historical backfill")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
