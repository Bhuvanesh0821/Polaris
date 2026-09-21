# =========================================================================
# POLARIS backend - production image
#
# Multi-stage so the runtime image carries no compilers: scikit-learn and
# psycopg build wheels in stage 1, and only the installed packages are
# copied forward.
# =========================================================================

# ---------- stage 1: build wheels ----------
FROM python:3.12-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt


# ---------- stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    ENVIRONMENT=production \
    DEBUG=false

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 polaris

COPY --from=build /opt/venv /opt/venv
COPY . .

# Trained models are written here at runtime.
RUN mkdir -p /app/artifacts && chown -R polaris:polaris /app

# Never run the API as root.
USER polaris

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD curl -fsS http://localhost:8000/api/health || exit 1

# $PORT is respected so the same image runs unchanged on Render/Railway,
# which inject the port at run time.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
