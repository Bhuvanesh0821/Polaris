"""POLARIS - SQLAlchemy engine, session factory and declarative base."""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

log = logging.getLogger("polaris.db")


class Base(DeclarativeBase):
    """Declarative base for every POLARIS ORM model."""


engine = create_engine(
    settings.sqlalchemy_url,
    echo=settings.db_echo,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency - one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Transactional scope for scripts and background work."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def ping() -> tuple[bool, str]:
    """Cheap liveness probe used by /api/health."""
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar_one()
        return True, str(version).split(",")[0]
    except Exception as exc:  # pragma: no cover - surfaced through /health
        return False, f"{type(exc).__name__}: {exc}"


def create_all() -> None:
    """Create every table declared on Base. Idempotent."""
    from app import models  # noqa: F401  (registers the mappers)

    Base.metadata.create_all(bind=engine)
    log.info("POLARIS schema ensured on %s", settings.safe_dsn())


def drop_all() -> None:
    from app import models  # noqa: F401

    Base.metadata.drop_all(bind=engine)
