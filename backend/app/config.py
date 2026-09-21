"""POLARIS - application settings (12-factor, loaded from .env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
ARTIFACT_DIR = BACKEND_DIR / "artifacts"
ARTIFACT_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_name: str = "POLARIS"
    app_full_name: str = (
        "Polar Intelligent Energy Management & Resilience Intelligence System"
    )
    api_version: str = "1.0.0"
    #: Defaults to FALSE so a deployment that forgets to set it is safe rather
    #: than leaking tracebacks. Local development sets DEBUG=true in .env.
    debug: bool = False
    #: "development" | "production". Controls CORS strictness and docs exposure.
    environment: str = "development"
    api_prefix: str = "/api"
    #: Expose /docs and /redoc. Safe here (no auth, read-mostly API) but made
    #: explicit so it can be switched off.
    enable_docs: bool = True

    # --- PostgreSQL ---
    postgres_user: str = "polaris"
    postgres_password: str = ""
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "polaris"
    database_url_override: str = Field(default="", alias="DATABASE_URL")
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- CORS ---
    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:4173,http://127.0.0.1:4173,"
        "http://localhost:3000,http://127.0.0.1:3000"
    )

    # --- Weather ingestion (REAL DATA) ---
    weather_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    weather_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    weather_timeout_s: float = 25.0
    #: Master switch for outbound provider calls. When false POLARIS makes no
    #: network requests and reports the resulting staleness honestly - it does
    #: NOT fall back to generated weather.
    allow_network_ingest: bool = True

    # --- Model / simulation ---
    #: days of real weather history loaded for training and state estimation
    history_days: int = 120
    #: hours ahead the AI forecast and optimiser plan for. 168 h = 7 days,
    #: which is the longest horizon the dashboard offers.
    forecast_horizon_h: int = 168
    random_seed: int = 20260921
    model_dir: str = str(ARTIFACT_DIR)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url_override:
            url = self.database_url_override
            # Normalise to the psycopg 3 driver used by this project.
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+psycopg://", 1)
            elif url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+psycopg://", 1)
            return url
        from urllib.parse import quote_plus

        pwd = quote_plus(self.postgres_password)
        return (
            f"postgresql+psycopg://{self.postgres_user}:{pwd}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in ("production", "prod")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_allow_origin_regex(self) -> str | None:
        """Permit any localhost port in development only.

        In production this MUST be None: a wildcard localhost regex on a
        public deployment would let any page served from a developer's own
        machine call the production API with credentials attached.
        """
        if self.is_production:
            return None
        return r"http://(localhost|127\.0\.0\.1):\d+"

    def safe_dsn(self) -> str:
        """DSN with the password masked - safe to log or return from /health."""
        return (
            f"postgresql://{self.postgres_user}:***@{self.postgres_host}"
            f":{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
