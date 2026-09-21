"""Initialise the POLARIS database: schema + station rows.

    python -m scripts.init_db            # create tables + stations
    python -m scripts.init_db --reset    # drop everything first
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.core.station import (  # noqa: E402
    BATTERY,
    FUEL,
    GENERATOR,
    NO_TELEMETRY_NOTE,
    REFERENCE_STATIONS,
    SITE,
    SOLAR,
    WIND,
)
from app.database.session import create_all, drop_all, engine, session_scope  # noqa: E402
from app.models import Station, StationKind  # noqa: E402
from sqlalchemy import select  # noqa: E402


def provision_stations() -> None:
    with session_scope() as db:
        primary = db.scalar(select(Station).where(Station.code == SITE.station_code))
        if primary is None:
            primary = Station(code=SITE.station_code)
            db.add(primary)

        primary.name = SITE.station_name
        primary.operator = SITE.operator
        primary.kind = StationKind.MODELLED_PLANT
        primary.source_station_code = SITE.wmo_index
        primary.latitude = SITE.latitude
        primary.longitude = SITE.longitude
        primary.elevation_m = SITE.elevation_m
        primary.timezone = SITE.timezone
        primary.wind_rated_kw = WIND.rated_kw
        primary.solar_rated_kwp = SOLAR.rated_kwp
        primary.battery_nominal_kwh = BATTERY.nominal_capacity_kwh
        primary.generator_rated_kw = GENERATOR.rated_kw
        primary.fuel_capacity_l = FUEL.tank_capacity_l
        primary.summer_crew = SITE.summer_crew
        primary.winter_crew = SITE.winter_crew
        primary.is_active = True
        primary.notes = (
            f"Real WMO-reporting station (index {SITE.wmo_index}), "
            f"{SITE.region}. Weather is REAL. "
        ) + NO_TELEMETRY_NOTE

        for ref in REFERENCE_STATIONS:
            row = db.scalar(select(Station).where(Station.code == ref.code))
            if row is None:
                row = Station(code=ref.code)
                db.add(row)
            row.name = ref.name
            row.operator = ref.operator
            row.kind = StationKind.REAL_OBSERVATION
            row.source_station_code = ref.wmo_index or ref.icao
            row.latitude = ref.latitude
            row.longitude = ref.longitude
            row.elevation_m = ref.elevation_m
            row.timezone = "UTC"
            row.is_active = True
            row.notes = ref.note

        print(f"  station  {SITE.station_code:<10} {SITE.station_name} "
              f"(WMO {SITE.wmo_index})  [MODELLED_PLANT]")
        for ref in REFERENCE_STATIONS:
            print(f"  station  {ref.code:<10} {ref.name}  [REAL_OBSERVATION]")


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialise the POLARIS database")
    parser.add_argument("--reset", action="store_true",
                        help="drop all tables before creating them")
    args = parser.parse_args()

    print("=" * 68)
    print("POLARIS database initialisation")
    print("=" * 68)
    print(f"  target: {settings.safe_dsn()}")

    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception as exc:
        print(f"\n  ERROR: cannot connect to PostgreSQL.\n  {type(exc).__name__}: {exc}")
        print("\n  Check that the server is running and backend/.env is correct.")
        print("  See README.md > Database setup.")
        return 1

    if args.reset:
        print("  dropping existing tables ...")
        drop_all()

    print("  creating schema ...")
    create_all()
    print("  provisioning stations ...")
    provision_stations()
    print("\n  Done. Next: python -m scripts.bootstrap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
