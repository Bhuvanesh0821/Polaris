"""Configuration and secret-handling."""

from __future__ import annotations

import importlib



def _settings(monkeypatch, **env):
    """Build a fresh Settings with a controlled environment."""
    import app.config as cfg

    for k in ("DATABASE_URL", "ENVIRONMENT", "CORS_ORIGINS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(cfg)
    return cfg.Settings()


MANAGED = "postgresql://polaris:sup3rs3cret@dpg-x.oregon-postgres.render.com:5432/polaris_x"


def test_safe_dsn_never_leaks_the_password(monkeypatch):
    """/health returns this string publicly, so it must never carry a secret."""
    s = _settings(monkeypatch, DATABASE_URL=MANAGED)
    dsn = s.safe_dsn()
    assert "sup3rs3cret" not in dsn
    assert "***" in dsn


def test_safe_dsn_reports_the_host_actually_in_use(monkeypatch):
    """Reporting localhost while connected to a managed database would make
    the one endpoint used to diagnose connectivity actively misleading."""
    s = _settings(monkeypatch, DATABASE_URL=MANAGED)
    dsn = s.safe_dsn()
    assert "render.com" in dsn
    assert "localhost" not in dsn


def test_safe_dsn_falls_back_to_discrete_parts(monkeypatch):
    s = _settings(monkeypatch)
    assert "***" in s.safe_dsn()


def test_managed_url_is_normalised_to_psycopg(monkeypatch):
    """Providers hand out postgres:// or postgresql://; SQLAlchemy needs the
    explicit psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        url = MANAGED.replace("postgresql://", prefix)
        s = _settings(monkeypatch, DATABASE_URL=url)
        assert s.sqlalchemy_url.startswith("postgresql+psycopg://")


def test_debug_defaults_off(monkeypatch):
    """A deployment that forgets to set DEBUG must not leak tracebacks."""
    monkeypatch.delenv("DEBUG", raising=False)
    import app.config as cfg
    importlib.reload(cfg)
    assert cfg.Settings(_env_file=None).debug is False


def test_production_disables_the_localhost_cors_wildcard(monkeypatch):
    """On a public API the wildcard would let any page served from a
    developer's own machine call it with credentials attached."""
    prod = _settings(monkeypatch, ENVIRONMENT="production")
    assert prod.is_production is True
    assert prod.cors_allow_origin_regex is None

    dev = _settings(monkeypatch, ENVIRONMENT="development")
    assert dev.cors_allow_origin_regex is not None


def test_cors_origins_parse_into_a_list(monkeypatch):
    s = _settings(monkeypatch, CORS_ORIGINS="https://a.example, https://b.example")
    assert s.cors_origin_list == ["https://a.example", "https://b.example"]
