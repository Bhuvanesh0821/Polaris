"""Retention pruning keeps the hosted database bounded without losing data
anything still reads.

Runs against the local PostgreSQL inside a transaction that is rolled back,
so the development database is left exactly as it was.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def db():
    from app.database.session import engine, ping

    ok, _ = ping()
    if not ok:
        pytest.skip("PostgreSQL not reachable")
    conn = engine.connect()
    outer = conn.begin()
    # prune_old_data() commits; bound this way its commit only releases a
    # savepoint and the outer rollback still discards everything.
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


def _station_id(db) -> int:
    from app.models import Station

    sid = db.scalar(select(Station.id).limit(1))
    if sid is None:
        pytest.skip("no station seeded")
    return sid


def test_prunes_expired_rows_and_keeps_recent_ones(db):
    from app.models import IngestionRun
    from app.services.retention import RUN_LOG_DAYS, prune_old_data

    now = datetime.now(timezone.utc)
    old = IngestionRun(started_at=now - timedelta(days=RUN_LOG_DAYS + 5))
    recent = IngestionRun(started_at=now - timedelta(days=1))
    db.add_all([old, recent])
    db.flush()
    old_id, recent_id = old.id, recent.id

    removed = prune_old_data(db, now=now)

    assert removed.get("ingestion_runs", 0) >= 1
    assert db.get(IngestionRun, old_id) is None
    assert db.get(IngestionRun, recent_id) is not None


def test_never_prunes_an_unresolved_alert(db):
    """An old alert that is still ACTIVE is an open problem, not history."""
    from app.models import Alert, AlertSeverity, AlertStatus
    from app.services.retention import RUN_LOG_DAYS, prune_old_data

    sid = _station_id(db)
    now = datetime.now(timezone.utc)
    ancient = now - timedelta(days=RUN_LOG_DAYS + 60)

    def alert(status):
        return Alert(station_id=sid, raised_at=ancient, code="TEST_RETENTION",
                     severity=AlertSeverity.WARNING, status=status,
                     title="retention test", message="retention test")

    active, resolved = alert(AlertStatus.ACTIVE), alert(AlertStatus.RESOLVED)
    db.add_all([active, resolved])
    db.flush()
    active_id, resolved_id = active.id, resolved.id

    prune_old_data(db, now=now)

    assert db.get(Alert, active_id) is not None
    assert db.get(Alert, resolved_id) is None


def test_weather_window_outlives_the_training_window():
    """Pruning weather the models still train on would silently shrink
    the training set."""
    from app.config import settings
    from app.services.retention import WEATHER_DAYS

    assert WEATHER_DAYS > settings.history_days
