"""POLARIS - Polar Intelligent Energy Management & Resilience Intelligence System.

FastAPI application entry point.

    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import energy, intelligence, system, weather
from app.config import settings
from app.core.station import DATA_BANNER, MODEL_DISCLAIMER, NO_TELEMETRY_NOTE
from app.database.session import ping
from app.schemas.common import ErrorResponse

logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)-24s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("polaris")

DESCRIPTION = f"""
**{settings.app_full_name}**

AI-driven energy management and resilience decision support for polar
research stations.

### Data honesty

{DATA_BANNER}

POLARIS separates four kinds of data and never blurs them:

| Class | Meaning |
|---|---|
| `REAL_LIVE_WEATHER` | Measured observations from external providers. Maitri (WMO 89514), an Indian Antarctic Programme station, is the authoritative source. |
| `MODELLED_ENERGY` | Battery, fuel, generator and load estimates from the research-based energy model. **Not telemetry.** |
| `AI_FORECAST` | scikit-learn predictions driven by real weather inputs. |
| `SIMULATED_SCENARIO` | Crisis what-ifs. Never live station telemetry. |

{NO_TELEMETRY_NOTE}

*{MODEL_DISCLAIMER}*
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("=" * 68)
    log.info("POLARIS %s starting", settings.api_version)
    log.info("%s", DATA_BANNER)
    log.info("=" * 68)

    ok, info = ping()
    if ok:
        log.info("PostgreSQL connected: %s", info)
    else:
        log.error("PostgreSQL NOT reachable: %s", info)
        log.error("DSN: %s", settings.safe_dsn())

    # Restore previously trained models so a restart is not a cold start.
    from app.ml.registry import registry

    if registry.try_restore():
        log.info("Restored trained models from disk")

    from app.services import scheduler

    scheduler.start(settings.refresh_interval_s)
    log.info("Background refresh scheduler started (%ds interval)",
             scheduler.state.interval_s)

    yield

    await scheduler.stop()
    log.info("POLARIS stopped")


app = FastAPI(
    title=settings.app_name,
    description=DESCRIPTION,
    version=settings.api_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url="/redoc" if settings.enable_docs else None,
    openapi_url="/openapi.json" if settings.enable_docs else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # None in production - see config.cors_allow_origin_regex for why.
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Polaris-Data-Class"],
    max_age=600,
)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


#: Starlette renamed this constant; resolve it once so the app works on both
#: the old and new versions without emitting a deprecation warning per request.
HTTP_422 = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", None) or 422


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=HTTP_422,
        content=ErrorResponse(
            error="Validation failed",
            detail="; ".join(
                f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}"
                for e in exc.errors()
            ),
            code="VALIDATION_ERROR",
            path=str(request.url.path),
            timestamp=datetime.now(timezone.utc),
        ).model_dump(mode="json"),
    )


@app.exception_handler(SQLAlchemyError)
async def db_handler(request: Request, exc: SQLAlchemyError):
    log.exception("Database error on %s", request.url.path)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=ErrorResponse(
            error="Database unavailable",
            detail=(
                "PostgreSQL could not serve this request. Check that the "
                "server is running and that backend/.env is correct."
            ),
            code="DB_ERROR",
            path=str(request.url.path),
            timestamp=datetime.now(timezone.utc),
        ).model_dump(mode="json"),
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error="Internal server error",
            detail=f"{type(exc).__name__}: {exc}" if settings.debug else None,
            code="INTERNAL_ERROR",
            path=str(request.url.path),
            timestamp=datetime.now(timezone.utc),
        ).model_dump(mode="json"),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(system.router, prefix=settings.api_prefix)
app.include_router(weather.router, prefix=settings.api_prefix)
app.include_router(energy.router, prefix=settings.api_prefix)
app.include_router(intelligence.router, prefix=settings.api_prefix)


@app.get("/", tags=["system"])
def root():
    return {
        "app": settings.app_name,
        "full_name": settings.app_full_name,
        "version": settings.api_version,
        "data_banner": DATA_BANNER,
        "model_disclaimer": MODEL_DISCLAIMER,
        "docs": "/docs",
        "health": f"{settings.api_prefix}/health",
        "stack": "Python | FastAPI | React | PostgreSQL | JavaScript | HTML/CSS",
    }
