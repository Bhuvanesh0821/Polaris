"""Background refresh scheduler.

Runs the live ingestion + intelligence pipeline on a fixed interval without
blocking the API. Exposes its own state so the dashboard can show when the
next refresh is due and whether the last one succeeded.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.database.session import SessionLocal
from app.services.pipeline import backfill_history_if_needed, run_pipeline
from app.services.weather.ingest import run_ingestion

log = logging.getLogger("polaris.scheduler")


@dataclass
class SchedulerState:
    running: bool = False
    interval_s: int = 900  # 15 minutes
    started_at: datetime | None = None
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    next_run_at: datetime | None = None
    run_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    last_report: dict = field(default_factory=dict)
    in_progress: bool = False

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "in_progress": self.in_progress,
            "interval_seconds": self.interval_s,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_success_at": (
                self.last_success_at.isoformat() if self.last_success_at else None
            ),
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "seconds_until_next": (
                max(0, int((self.next_run_at - datetime.now(timezone.utc)).total_seconds()))
                if self.next_run_at else None
            ),
            "run_count": self.run_count,
            "failure_count": self.failure_count,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "last_report": self.last_report,
        }


state = SchedulerState()
_task: asyncio.Task | None = None
_lock = asyncio.Lock()


async def refresh_once(trigger: str = "scheduled", run_ai: bool = True) -> dict:
    """One full cycle: ingest live weather, then run the AI pipeline.

    Serialised by a lock so a manual refresh cannot collide with the timer.
    """
    async with _lock:
        state.in_progress = True
        started = datetime.now(timezone.utc)
        state.last_run_at = started
        state.run_count += 1
        out: dict = {"trigger": trigger, "started_at": started.isoformat()}

        db = SessionLocal()
        try:
            # Self-heal a thin history store. No-op once enough real history
            # exists; retries if an earlier attempt was rate-limited.
            try:
                topped_up = await backfill_history_if_needed(db)
                if topped_up:
                    out["backfill"] = {"observations_written": topped_up}
            except Exception as exc:
                log.warning("History top-up skipped: %s", exc)

            ingest = await run_ingestion(db, trigger=trigger)
            out["ingestion"] = ingest.as_dict()

            if ingest.any_success and run_ai:
                # The AI stage is synchronous and CPU-bound; keep the event
                # loop responsive by running it in a worker thread.
                pipeline = await asyncio.to_thread(run_pipeline, db, trigger)
                out["pipeline"] = pipeline.as_dict()
                ok = pipeline.ok
                err = pipeline.error
            else:
                ok = ingest.any_success
                err = ingest.error
                if not ingest.any_success:
                    out["pipeline"] = {
                        "ok": False,
                        "error": "Skipped - no live weather was ingested. "
                                 "POLARIS does not run the model on "
                                 "fabricated inputs.",
                    }

            if ok:
                state.last_success_at = datetime.now(timezone.utc)
                state.consecutive_failures = 0
                state.last_error = None
            else:
                state.failure_count += 1
                state.consecutive_failures += 1
                state.last_error = err
            out["ok"] = ok
        except Exception as exc:
            state.failure_count += 1
            state.consecutive_failures += 1
            state.last_error = f"{type(exc).__name__}: {exc}"
            out["ok"] = False
            out["error"] = state.last_error
            log.exception("Refresh cycle failed")
        finally:
            db.close()
            state.in_progress = False
            out["duration_ms"] = round(
                (datetime.now(timezone.utc) - started).total_seconds() * 1000, 1
            )
            state.last_report = out
        return out


async def _loop() -> None:
    log.info("Scheduler started (interval %ds)", state.interval_s)
    # Small delay so the API is serving before the first heavy cycle.
    await asyncio.sleep(2)
    while state.running:
        try:
            await refresh_once(trigger="scheduled")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Unhandled scheduler error")

        # Back off when a source is persistently down, so a dead provider is
        # not hammered every 15 minutes.
        delay = state.interval_s
        if state.consecutive_failures > 2:
            delay = min(state.interval_s * 4, 3600)
            log.warning("Backing off to %ds after %d consecutive failures",
                        delay, state.consecutive_failures)

        state.next_run_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise


def start(interval_s: int | None = None) -> None:
    global _task
    if state.running:
        return
    if interval_s:
        state.interval_s = interval_s
    state.running = True
    state.started_at = datetime.now(timezone.utc)
    _task = asyncio.create_task(_loop(), name="polaris-refresh")


async def stop(timeout_s: float = 5.0) -> None:
    """Cancel the refresh loop, but never block shutdown indefinitely.

    A cycle can be parked inside `asyncio.to_thread(run_pipeline)`, and
    cancelling the awaiting task does not interrupt the worker thread. Without
    a bound, shutdown (and therefore uvicorn's --reload) hangs forever.
    """
    global _task
    state.running = False
    if _task is not None:
        _task.cancel()
        try:
            await asyncio.wait_for(_task, timeout=timeout_s)
        except asyncio.TimeoutError:
            log.warning(
                "Scheduler did not stop within %.0fs (a refresh is still "
                "running in a worker thread); continuing shutdown.", timeout_s
            )
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Scheduler raised while stopping")
        _task = None
    log.info("Scheduler stopped")
