# POLARIS

**Polar Intelligent Energy Management & Resilience Intelligence System**

AI-driven smart energy management for polar research stations.

`Python | FastAPI | React | PostgreSQL | JavaScript | HTML/CSS`

---

## REAL WEATHER DATA + RESEARCH-BASED ENERGY MODEL

POLARIS is built around one rule: **never present a modelled number as a measured one.**

Four data classes are distinguished everywhere — in the database, in the API
response envelope, and as a coloured tag on every panel of the dashboard:

| Class | What it is | Example |
|---|---|---|
| **REAL LIVE WEATHER** | Measured observations pulled from external meteorological providers | temperature, wind speed & direction, humidity, solar radiation, pressure |
| **MODELLED** | Research-based energy model output | battery state of charge, fuel level, generator state, station load, autonomy |
| **AI FORECAST** | scikit-learn predictions driven by real weather inputs | load forecast, renewable generation forecast |
| **SIMULATED** | Crisis what-if scenarios | storm / generator-failure projections |

> **Station energy parameters are modelled/estimated for research and
> demonstration purposes.**

**There is no public real-time electrical telemetry feed for Maitri, Bharati, or
any other Indian Antarctic station.** POLARIS does not claim otherwise. Battery
SoC, fuel level, generator state and station load are physics-model estimates
driven by the live weather — they are labelled `MODELLED` in every table, every
API response and every UI panel.

Equally, POLARIS **never fabricates weather**. There is deliberately no synthetic
weather generator. If every live provider fails, the API reports
`STALE`/`FAILED`, shows *"Live source unavailable — displaying last successful
observation"* with its timestamp, and re-serves the last genuine reading.

---

## Live data sources

POLARIS drives its model from **Maitri (WMO 89514)** — an Indian Antarctic
Programme station (NCPOR / IMD) in the Schirmacher Oasis at 70.7666 °S, 11.75 °E.
Maitri transmits FM-12 SYNOP bulletins onto the WMO Global Telecommunication
System, which are publicly retrievable in near real time.

| Priority | Provider | Role | Cadence | Notes |
|---|---|---|---|---|
| 10 | **IMD/NCPOR Maitri SYNOP** via OGIMET (WMO 89514) | Authoritative station observation | 6-hourly (00/06/12/18 UTC) | Decoded by a purpose-built FM-12 SYNOP parser. Carries **no** radiation group and Maitri omits the dewpoint group. |
| 20 | **Open-Meteo** (ECMWF/GFS) at Maitri's coordinates | Hourly resolution, solar radiation, forecast horizon | hourly | Supplies the two things SYNOP cannot. |
| 25 | **MET Norway Locationforecast** at Maitri's coordinates | Independent forecast failover | hourly to ~60 h, then 6-hourly | Keeps the forecast alive when Open-Meteo is rate-limited. The 6-hourly tail is interpolated to hourly (flagged); no radiation, so solar is estimated from cloud cover. |
| 30 | **NOAA Aviation Weather METAR** (NZSP Amundsen-Scott) | Independent failover + regional reference | hourly–6-hourly | Real aerodrome observations. |

Values are merged **per field**, with the authoritative source winning for every
variable it actually reports. Each value on the dashboard carries its own source
attribution underneath it.

> **A note on Bharati.** Bharati, India's other Antarctic station, does **not**
> publish a public real-time feed. WMO 89574 — a plausible-looking guess — is
> actually *Progress*, the Russian station ~7 km away in the Larsemann Hills.
> POLARIS registers such neighbours as clearly-labelled regional reference
> stations and never presents their data as Bharati's.

---

## Quick start (Windows)

### 0. Prerequisites

- **Python 3.12+** (3.13/3.14 fine) — `python --version`
- **Node.js 18+** — `node --version`
- **PostgreSQL 14+** running locally — the installer's service is enough
- Outbound internet access for the live weather providers

### 1. Database setup — run this once

```bash
powershell -ExecutionPolicy Bypass -File database\setup_database.ps1
```

It asks for your **PostgreSQL superuser password** (used only to create the role,
never stored), asks you to choose a password for a new `polaris` role, creates
the `polaris` database, and writes `backend\.env` for you.

<details>
<summary>Prefer to do it by hand?</summary>

```sql
CREATE ROLE polaris LOGIN PASSWORD 'your_password';
CREATE DATABASE polaris OWNER polaris ENCODING 'UTF8';
GRANT ALL ON SCHEMA public TO polaris;
```

Then copy `backend/.env.example` to `backend/.env` and fill in
`POSTGRES_PASSWORD`.
</details>

### 2. Backend

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create the schema and register the stations:

```bash
.\.venv\Scripts\python.exe -m scripts.init_db
```

Pull real data and run the whole pipeline once:

```bash
.\.venv\Scripts\python.exe -m scripts.bootstrap
```

This backfills ~120 days of **real ERA5 reanalysis** (so the ML models have
genuine history to learn from), pulls **live** conditions from Maitri, trains the
models, and produces the first forecast, optimisation and recommendations.

Start the API:

```bash
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

- API docs — <http://localhost:8000/docs>
- Health — <http://localhost:8000/api/health>

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

### 4. Tests

```bash
cd backend
.\.venv\Scripts\python.exe -m pytest
```

154 tests. The physics/ML/crisis suites run standalone; the API suite
auto-skips unless POLARIS is live on `:8000` (override with `POLARIS_API_BASE`).

| Suite | Tests | Covers |
|---|---|---|
| `test_physics.py` | 22 | solar geometry, turbine curve, fuel model, battery limits |
| `test_models.py` | 31 | load model, ML forecasting, state estimation, survival, risk |
| `test_crisis.py` | 37 | all 7 scenarios, operator overrides, before/after |
| `test_api.py` | 46 | every endpoint, data-honesty labelling, validation |
| `test_config.py` | 9 | secret masking, CORS, database URL handling |
| `test_met_norway.py` | 6 | forecast failover, interpolation, radiation estimate |
| `test_retention.py` | 3 | bounded storage without losing data still in use |

Open <http://localhost:5173> in your browser.

> POLARIS is a **website that runs in your browser**. There is nothing to
> install on a phone and no desktop app — open the URL in Chrome, Edge or
> Firefox.

### Docker alternative

```bash
docker compose up --build
```

---

## Architecture

```
REAL-WORLD ANTARCTIC DATA        Maitri SYNOP (WMO 89514) · Open-Meteo · MET Norway · NOAA METAR
          |
DATA INGESTION & PREPROCESSING   provider chain, retry + backoff, per-field merge,
          |                      raw payload stored for audit
DATA PROCESSING & FEATURE ENG.   solar geometry, wind chill, air density, HDD,
          |                      lags, rolling means, clear-sky index
AI ENERGY INTELLIGENCE ENGINE
   |-- AI LOAD FORECASTING       HistGradientBoostingRegressor + 80% intervals
   |-- RENEWABLE PREDICTION      physics baseline + RandomForest residual correction
   |-- ENERGY STATE ESTIMATION   hour-stepping balance: battery, fuel, gensets
          |
ENERGY OPTIMIZATION              receding-horizon merit-order dispatch
   |-- CRITICAL LOAD PRIORITISATION   P1 -> P4 shed ladder, P1 never shed
   |-- FUEL OPTIMIZATION              Willans-line burn, wet-stacking avoidance
          |
ENERGY SURVIVAL ANALYSIS         3 postures: normal / critical-only / crisis
          |
OPERATIONAL RISK ASSESSMENT      8 weighted factors -> LOW..SEVERE, explainable
          |
POLAR CRISIS SIMULATOR           7 scenarios + operator sliders, before/after
          |
AI DECISION & RECOMMENDATION     ranked actions with drivers + counterfactuals
          |
EXPLAINABLE AI                   global permutation importance, local occlusion
          |                      attribution, causal dispatch narrative
OPERATIONAL DASHBOARD            React + Recharts, 12 pages
          |
SAFE & EFFICIENT POLAR STATION OPERATION
```

### The live loop

Every 30 minutes (and on the **Refresh** button), the backend runs:

```
live weather  ->  preprocess  ->  features  ->  AI forecast
              ->  renewable prediction  ->  state estimation
              ->  optimisation  ->  survival analysis  ->  recommendations
              ->  PostgreSQL
```

If ingestion fails, the AI stage is **skipped rather than run on stale or
invented inputs**, and the failure is recorded in `ingestion_runs`.

---

## Project structure

```
polaris/
├── backend/
│   ├── app/
│   │   ├── api/routes/       weather · energy · intelligence · system
│   │   ├── core/             station.py (plant config) · physics.py
│   │   ├── database/         SQLAlchemy engine + session
│   │   ├── models/           12 tables + stations + source health
│   │   ├── schemas/          Pydantic request/response models
│   │   ├── services/
│   │   │   ├── weather/      synop.py · providers.py · ingest.py
│   │   │   ├── energy_model.py  pipeline.py  scheduler.py
│   │   │   └── recommendations.py
│   │   ├── ml/               load_forecaster · renewable_forecaster
│   │   │                     energy_state_estimator · anomaly_detector
│   │   │                     explainability · features · registry
│   │   ├── optimization/     energy_optimizer.py
│   │   ├── simulation/       crisis_simulator.py
│   │   └── main.py
│   ├── scripts/              init_db.py · bootstrap.py
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   └── src/
│       ├── pages/            12 pages
│       ├── components/       Primitives.jsx (cards, tags, live pill)
│       ├── charts/           Charts.jsx (Recharts wrappers)
│       ├── layouts/          AppLayout.jsx
│       ├── services/         api.js
│       ├── hooks/            usePolaris.jsx
│       ├── utils/            format.js
│       └── styles/           theme.css · app.css
├── database/
│   ├── schema.sql            reference DDL
│   └── setup_database.ps1    one-command setup
├── docker-compose.yml
└── README.md
```

---

## API

| Method | Endpoint | Data class |
|---|---|---|
| GET | `/api/ping` | — (liveness, no database) |
| GET | `/api/health` | — |
| GET | `/api/weather` | REAL LIVE |
| GET | `/api/weather/current` | REAL LIVE |
| POST | `/api/weather/refresh` | REAL LIVE |
| GET | `/api/weather/sources` | — |
| GET | `/api/weather/providers` | — |
| GET | `/api/energy/status` | MODELLED |
| GET | `/api/energy/history` | MODELLED |
| GET | `/api/energy/load-profile` | MODELLED |
| GET | `/api/load/forecast` | AI FORECAST |
| GET | `/api/renewable/forecast` | AI FORECAST |
| POST | `/api/optimization/run` | MODELLED |
| GET | `/api/optimization/latest` | MODELLED |
| GET | `/api/survival-analysis` | MODELLED — includes risk level + 3 autonomy modes |
| GET | `/api/crisis/scenarios` | SIMULATED |
| POST | `/api/crisis/simulate` | SIMULATED |
| GET | `/api/recommendations` | AI FORECAST |
| GET | `/api/alerts` | MODELLED |
| GET | `/api/explainability` | AI FORECAST |
| GET | `/api/dashboard/summary` | mixed (each section tagged) |
| GET | `/api/model/info` | — |
| POST | `/api/model/retrain` | — |

Every domain response is wrapped in an envelope carrying its provenance:

```json
{
  "data": { },
  "provenance": {
    "data_class": "MODELLED_ENERGY",
    "label": "RESEARCH-BASED ENERGY MODEL",
    "is_measured": false,
    "disclaimer": "Station energy parameters are modelled/estimated..."
  },
  "generated_at": "2026-09-21T14:22:45Z"
}
```

---

## The energy model

All plant parameters live in `backend/app/core/station.py` — one file, so the
model and the dashboard can never disagree.

| Asset | Modelled specification |
|---|---|
| Wind | 6 × 10 kW, cut-in 3 m/s, rated 12 m/s, cut-out 25 m/s, hub 18 m, air-density corrected, rime-icing derate |
| Solar PV | 50 kWp, 70° tilt (sheds snow, catches low sun), north-facing, −0.38 %/°C, snow-albedo gain |
| Battery | 600 kWh, 15–95 % SoC window, 92 % round trip, temperature-derated capacity |
| Generators | 3 × 100 kW, Willans-line fuel model, 30 % minimum loading (wet stacking) |
| Fuel | 80 000 L Antarctic blend, 9.79 kWh/L, 12 000 L emergency reserve |

**Load priority ladder** — shedding walks upward from P4; **P1 is never shed**:

- **P1 Life Critical** — space heating, water & sanitation, medical, emergency comms
- **P2 Science Critical** — observatory instruments, −80 °C sample storage, data uplink
- **P3 Operational** — galley, lighting, workshop
- **P4 Deferrable** — snow melter, vehicle charging, batch lab work, recreation

Some physics worth noting, because it drives the interesting behaviour:

- **Cold air is dense.** At −30 °C the air is ~20 % denser than ISA, so a turbine
  produces ~20 % more power at the same wind speed.
- **Storms take the wind farm offline.** Above 25 m/s the turbines must feather to
  survive — so wind generation drops to zero *exactly* when wind chill is driving
  heating demand to its peak. That coupling is the single nastiest failure mode
  for a polar station, and it is what the Severe Storm scenario exists to show.
- **Light-loaded diesels waste fuel and destroy themselves.** Below ~30 % loading
  a genset wet-stacks; specific consumption rises from ~0.28 to ~0.42 L/kWh.
  The optimiser consolidates load onto fewer units rather than idling several.

---

## Troubleshooting

**`Cannot reach the POLARIS backend`** — start it:
`cd backend && .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000`

**`Database unavailable` / `password authentication failed`** — check
`backend/.env`, or re-run `database\setup_database.ps1`.

**Dashboard shows `STALE` or `SOURCE DOWN`** — POLARIS could not reach any live
provider. It is showing the last genuine observation with its timestamp, which is
the intended behaviour. Check internet access and press **Refresh**. OGIMET also
rate-limits; the chain fails over to Open-Meteo automatically.

**`404 — No modelled energy state yet`** — the pipeline has not run. Press
**Refresh** in the header, or run `python -m scripts.bootstrap`.

**Models not trained** — needs ≥48 hours of real weather history. Run
`python -m scripts.bootstrap` to backfill, or wait for the scheduler to
accumulate it. Until then the system degrades to the deterministic physics model
and says so, rather than inventing a forecast.

---

## Licence & attribution

Built for the Smart India Hackathon. Weather data courtesy of the WMO Global
Telecommunication System (via OGIMET), Open-Meteo, the Norwegian
Meteorological Institute (MET Norway, CC BY 4.0), and the NOAA Aviation Weather
Center. Station energy parameters are modelled — see the data-honesty statement
above.
