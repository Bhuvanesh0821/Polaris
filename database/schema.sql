-- =========================================================================
-- POLARIS - PostgreSQL schema (reference DDL)
--
-- This file documents the schema. You do NOT need to run it: the backend
-- creates everything from the SQLAlchemy models with
--
--     python -m scripts.init_db
--
-- It is kept in sync with backend/app/models/ and is here so the data model
-- can be reviewed without reading Python.
--
-- DATA HONESTY
--   weather_observations  REAL measured data from external providers
--   data_source_status    health of those providers
--   everything else       MODELLED / AI-PREDICTED / SIMULATED
-- =========================================================================

-- ------------------------------------------------------------------ enums --
CREATE TYPE data_provenance AS ENUM (
    'LIVE_OBSERVED',   -- live measurement pulled just now
    'REAL_OBSERVED',   -- historical measurement / reanalysis
    'REAL_FORECAST',   -- NWP weather forecast
    'MODELLED',        -- physics/engineering model output
    'ML_PREDICTED',    -- scikit-learn model output
    'SIMULATED',       -- crisis scenario / what-if
    'OPTIMIZED'        -- optimiser dispatch decision
);
-- NOTE: there is deliberately no 'SYNTHETIC_WEATHER' member. POLARIS never
-- fabricates an observation.

CREATE TYPE station_kind     AS ENUM ('MODELLED_PLANT', 'REAL_OBSERVATION');
CREATE TYPE source_health    AS ENUM ('OK', 'DEGRADED', 'STALE', 'FAILED', 'UNKNOWN');
CREATE TYPE battery_mode     AS ENUM ('CHARGING','DISCHARGING','IDLE','RESERVE','PROTECTED');
CREATE TYPE generator_status AS ENUM ('OFFLINE','STARTING','RUNNING','STANDBY','FAULT','MAINTENANCE');
CREATE TYPE alert_severity   AS ENUM ('INFO','WARNING','CRITICAL','EMERGENCY');
CREATE TYPE alert_status     AS ENUM ('ACTIVE','ACKNOWLEDGED','RESOLVED');
CREATE TYPE scenario_type    AS ENUM (
    'NORMAL_OPERATION','SEVERE_STORM','LOW_SOLAR','WIND_FAILURE',
    'GENERATOR_FAILURE','HIGH_DEMAND','COMBINED_CRISIS'
);
CREATE TYPE recommendation_category AS ENUM (
    'LOAD_MANAGEMENT','GENERATION_DISPATCH','STORAGE_STRATEGY',
    'FUEL_CONSERVATION','MAINTENANCE','SAFETY'
);
CREATE TYPE urgency AS ENUM ('ROUTINE','ELEVATED','URGENT','IMMEDIATE');

-- --------------------------------------------------------------- stations --
CREATE TABLE stations (
    id                  SERIAL PRIMARY KEY,
    code                VARCHAR(32)  NOT NULL UNIQUE,
    name                VARCHAR(160) NOT NULL,
    operator            VARCHAR(160),
    kind                station_kind NOT NULL DEFAULT 'MODELLED_PLANT',
    source_station_code VARCHAR(32),
    -- REAL geography
    latitude            DOUBLE PRECISION NOT NULL,
    longitude           DOUBLE PRECISION NOT NULL,
    elevation_m         DOUBLE PRECISION DEFAULT 0,
    timezone            VARCHAR(64) DEFAULT 'UTC',
    -- MODELLED plant nameplate
    wind_rated_kw       DOUBLE PRECISION DEFAULT 0,
    solar_rated_kwp     DOUBLE PRECISION DEFAULT 0,
    battery_nominal_kwh DOUBLE PRECISION DEFAULT 0,
    generator_rated_kw  DOUBLE PRECISION DEFAULT 0,
    fuel_capacity_l     DOUBLE PRECISION DEFAULT 0,
    summer_crew         INTEGER DEFAULT 0,
    winter_crew         INTEGER DEFAULT 0,
    is_active           BOOLEAN DEFAULT TRUE,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_stations_kind ON stations (kind);

-- ------------------------------------------- weather_observations (REAL) --
CREATE TABLE weather_observations (
    id                     SERIAL PRIMARY KEY,
    station_id             INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    observed_at            TIMESTAMPTZ NOT NULL,
    -- measured variables
    temperature_c          DOUBLE PRECISION NOT NULL,
    apparent_temperature_c DOUBLE PRECISION,
    wind_speed_ms          DOUBLE PRECISION NOT NULL,
    wind_gust_ms           DOUBLE PRECISION,
    wind_direction_deg     DOUBLE PRECISION,
    -- NULL when the source cannot measure it (a SYNOP bulletin carries no
    -- radiation group) - POLARIS leaves it empty rather than inventing it
    solar_radiation_wm2    DOUBLE PRECISION,
    direct_radiation_wm2   DOUBLE PRECISION,
    diffuse_radiation_wm2  DOUBLE PRECISION,
    humidity_pct           DOUBLE PRECISION,
    pressure_hpa           DOUBLE PRECISION,
    cloud_cover_pct        DOUBLE PRECISION,
    snowfall_mm            DOUBLE PRECISION,
    -- POLARIS-derived (computed, not measured)
    wind_chill_c           DOUBLE PRECISION,
    air_density_kg_m3      DOUBLE PRECISION,
    is_polar_night         BOOLEAN DEFAULT FALSE,
    is_blizzard            BOOLEAN DEFAULT FALSE,
    -- provenance / audit trail
    provenance             data_provenance NOT NULL DEFAULT 'LIVE_OBSERVED',
    source                 VARCHAR(120) NOT NULL,
    source_provider        VARCHAR(48)  NOT NULL,
    source_station_code    VARCHAR(32),
    fetched_at             TIMESTAMPTZ NOT NULL,
    raw_payload            JSONB,          -- untouched provider response
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_weather_station_ts UNIQUE (station_id, observed_at)
);
CREATE INDEX ix_weather_station_ts_desc ON weather_observations (station_id, observed_at);
CREATE INDEX ix_weather_provenance      ON weather_observations (provenance);
CREATE INDEX ix_weather_fetched         ON weather_observations (fetched_at);

-- --------------------------------------------------- live-pipeline health --
CREATE TABLE data_source_status (
    id                       SERIAL PRIMARY KEY,
    provider_key             VARCHAR(48)  NOT NULL UNIQUE,
    provider_label           VARCHAR(160) NOT NULL,
    -- TEXT, not VARCHAR: a fully-expanded provider query string runs to
    -- several hundred characters and must not be silently truncated.
    endpoint                 TEXT,
    priority                 INTEGER DEFAULT 100,
    is_enabled               BOOLEAN DEFAULT TRUE,
    is_active_source         BOOLEAN DEFAULT FALSE,
    health                   source_health NOT NULL DEFAULT 'UNKNOWN',
    last_attempt_at          TIMESTAMPTZ,
    last_success_at          TIMESTAMPTZ,
    last_failure_at          TIMESTAMPTZ,
    last_error               TEXT,
    last_http_status         INTEGER,
    last_latency_ms          DOUBLE PRECISION,
    consecutive_failures     INTEGER DEFAULT 0,
    total_attempts           INTEGER DEFAULT 0,
    total_successes          INTEGER DEFAULT 0,
    total_observations       INTEGER DEFAULT 0,
    freshness_window_s       INTEGER DEFAULT 3600,
    supports_solar_radiation BOOLEAN DEFAULT TRUE,
    notes                    TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ingestion_runs (
    id                    SERIAL PRIMARY KEY,
    started_at            TIMESTAMPTZ NOT NULL,
    finished_at           TIMESTAMPTZ,
    duration_ms           DOUBLE PRECISION,
    trigger               VARCHAR(32) DEFAULT 'scheduled',
    provider_key          VARCHAR(48),
    succeeded             BOOLEAN DEFAULT FALSE,
    observations_written  INTEGER DEFAULT 0,
    observations_skipped  INTEGER DEFAULT 0,
    attempts              INTEGER DEFAULT 1,
    error                 TEXT,
    pipeline_ran          BOOLEAN DEFAULT FALSE,
    detail                JSONB
);
CREATE INDEX ix_ingest_started ON ingestion_runs (started_at);

-- ------------------------------------------- energy_parameters (MODELLED) --
CREATE TABLE energy_parameters (
    id                     SERIAL PRIMARY KEY,
    station_id             INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    weather_id             INTEGER REFERENCES weather_observations(id) ON DELETE SET NULL,
    recorded_at            TIMESTAMPTZ NOT NULL,
    total_load_kw          DOUBLE PRECISION NOT NULL,
    critical_load_kw       DOUBLE PRECISION NOT NULL,
    deferrable_load_kw     DOUBLE PRECISION DEFAULT 0,
    shed_load_kw           DOUBLE PRECISION DEFAULT 0,
    unserved_load_kw       DOUBLE PRECISION DEFAULT 0,
    wind_generation_kw     DOUBLE PRECISION DEFAULT 0,
    solar_generation_kw    DOUBLE PRECISION DEFAULT 0,
    generator_output_kw    DOUBLE PRECISION DEFAULT 0,
    renewable_curtailed_kw DOUBLE PRECISION DEFAULT 0,
    battery_charge_kw      DOUBLE PRECISION DEFAULT 0,
    battery_discharge_kw   DOUBLE PRECISION DEFAULT 0,
    battery_soc_pct        DOUBLE PRECISION DEFAULT 0,
    fuel_consumed_l        DOUBLE PRECISION DEFAULT 0,
    fuel_level_l           DOUBLE PRECISION DEFAULT 0,
    renewable_fraction     DOUBLE PRECISION DEFAULT 0,
    energy_autonomy_hours  DOUBLE PRECISION DEFAULT 0,
    co2_kg                 DOUBLE PRECISION DEFAULT 0,
    -- The measured weather that drove this modelled hour, stored on the row
    -- so it is self-describing: reading it back never risks pairing an old
    -- modelled hour with the current live observation.
    temperature_c          DOUBLE PRECISION,
    wind_speed_ms          DOUBLE PRECISION,
    is_anomaly             BOOLEAN DEFAULT FALSE,
    anomaly_score          DOUBLE PRECISION,
    provenance             data_provenance NOT NULL DEFAULT 'MODELLED',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_energy_station_ts UNIQUE (station_id, recorded_at)
);
CREATE INDEX ix_energy_station_ts ON energy_parameters (station_id, recorded_at);
CREATE INDEX ix_energy_anomaly    ON energy_parameters (is_anomaly);

-- ---------------------------------------------- load_profiles (MODELLED) --
CREATE TABLE load_profiles (
    id            SERIAL PRIMARY KEY,
    station_id    INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    recorded_at   TIMESTAMPTZ NOT NULL,
    channel_key   VARCHAR(64)  NOT NULL,
    channel_label VARCHAR(160) NOT NULL,
    priority      VARCHAR(32)  NOT NULL,   -- P1_LIFE_CRITICAL .. P4_DEFERRABLE
    demand_kw     DOUBLE PRECISION NOT NULL,
    served_kw     DOUBLE PRECISION NOT NULL,
    shed_kw       DOUBLE PRECISION DEFAULT 0,
    deferred_kw   DOUBLE PRECISION DEFAULT 0,
    is_deferrable BOOLEAN DEFAULT FALSE,
    provenance    data_provenance NOT NULL DEFAULT 'MODELLED',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_load_station_ts_ch UNIQUE (station_id, recorded_at, channel_key)
);
CREATE INDEX ix_load_station_ts ON load_profiles (station_id, recorded_at);
CREATE INDEX ix_load_priority   ON load_profiles (station_id, priority);

-- --------------------------------------------- battery_states (MODELLED) --
CREATE TABLE battery_states (
    id                     SERIAL PRIMARY KEY,
    station_id             INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    recorded_at            TIMESTAMPTZ NOT NULL,
    soc_pct                DOUBLE PRECISION NOT NULL,
    stored_kwh             DOUBLE PRECISION NOT NULL,
    usable_kwh             DOUBLE PRECISION NOT NULL,
    nominal_capacity_kwh   DOUBLE PRECISION NOT NULL,
    effective_capacity_kwh DOUBLE PRECISION NOT NULL,
    power_kw               DOUBLE PRECISION DEFAULT 0,
    mode                   battery_mode NOT NULL DEFAULT 'IDLE',
    temperature_c          DOUBLE PRECISION,
    temp_derate_factor     DOUBLE PRECISION DEFAULT 1,
    state_of_health_pct    DOUBLE PRECISION DEFAULT 100,
    cycles_equivalent      DOUBLE PRECISION DEFAULT 0,
    hours_to_reserve       DOUBLE PRECISION,
    provenance             data_provenance NOT NULL DEFAULT 'MODELLED',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_batt_station_ts UNIQUE (station_id, recorded_at)
);
CREATE INDEX ix_batt_station_ts ON battery_states (station_id, recorded_at);

-- ------------------------------------------- generator_states (MODELLED) --
CREATE TABLE generator_states (
    id                      SERIAL PRIMARY KEY,
    station_id              INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    recorded_at             TIMESTAMPTZ NOT NULL,
    unit_id                 INTEGER NOT NULL,
    unit_label              VARCHAR(48) DEFAULT 'DG',
    status                  generator_status NOT NULL DEFAULT 'OFFLINE',
    output_kw               DOUBLE PRECISION DEFAULT 0,
    rated_kw                DOUBLE PRECISION NOT NULL,
    loading_pct             DOUBLE PRECISION DEFAULT 0,
    fuel_rate_lph           DOUBLE PRECISION DEFAULT 0,
    specific_fuel_l_per_kwh DOUBLE PRECISION,
    efficiency_pct          DOUBLE PRECISION DEFAULT 0,
    running_hours_total     DOUBLE PRECISION DEFAULT 0,
    hours_to_service        DOUBLE PRECISION,
    starts_today            INTEGER DEFAULT 0,
    wet_stacking_risk       BOOLEAN DEFAULT FALSE,
    provenance              data_provenance NOT NULL DEFAULT 'MODELLED',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_gen_station_ts_unit UNIQUE (station_id, recorded_at, unit_id)
);
CREATE INDEX ix_gen_station_ts ON generator_states (station_id, recorded_at);
CREATE INDEX ix_gen_status     ON generator_states (status);

-- ------------------------------------------------ fuel_states (MODELLED) --
CREATE TABLE fuel_states (
    id                     SERIAL PRIMARY KEY,
    station_id             INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    recorded_at            TIMESTAMPTZ NOT NULL,
    level_l                DOUBLE PRECISION NOT NULL,
    capacity_l             DOUBLE PRECISION NOT NULL,
    level_pct              DOUBLE PRECISION NOT NULL,
    consumption_rate_lph   DOUBLE PRECISION DEFAULT 0,
    consumed_24h_l         DOUBLE PRECISION DEFAULT 0,
    days_remaining         DOUBLE PRECISION,
    days_to_resupply       DOUBLE PRECISION,
    energy_content_kwh     DOUBLE PRECISION DEFAULT 0,
    below_critical_reserve BOOLEAN DEFAULT FALSE,
    fuel_type              VARCHAR(48) DEFAULT 'Antarctic blend (SKO)',
    notes                  TEXT,
    detail                 JSONB,
    provenance             data_provenance NOT NULL DEFAULT 'MODELLED',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_fuel_station_ts UNIQUE (station_id, recorded_at)
);
CREATE INDEX ix_fuel_station_ts ON fuel_states (station_id, recorded_at);

-- --------------------------------------- renewable_forecasts (AI OUTPUT) --
CREATE TABLE renewable_forecasts (
    id                        SERIAL PRIMARY KEY,
    station_id                INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    run_at                    TIMESTAMPTZ NOT NULL,
    target_time               TIMESTAMPTZ NOT NULL,
    horizon_h                 INTEGER NOT NULL,
    wind_kw                   DOUBLE PRECISION DEFAULT 0,
    solar_kw                  DOUBLE PRECISION DEFAULT 0,
    total_kw                  DOUBLE PRECISION DEFAULT 0,
    wind_kw_p10               DOUBLE PRECISION,
    wind_kw_p90               DOUBLE PRECISION,
    solar_kw_p10              DOUBLE PRECISION,
    solar_kw_p90              DOUBLE PRECISION,
    wind_physical_kw          DOUBLE PRECISION,
    solar_physical_kw         DOUBLE PRECISION,
    ml_correction_kw          DOUBLE PRECISION,
    input_temperature_c       DOUBLE PRECISION,
    input_wind_speed_ms       DOUBLE PRECISION,
    input_solar_radiation_wm2 DOUBLE PRECISION,
    weather_provenance        VARCHAR(32),
    weather_source            VARCHAR(120),
    turbine_curtailed         BOOLEAN,
    icing_risk                BOOLEAN,
    model_version             VARCHAR(64),
    confidence                DOUBLE PRECISION,
    provenance                data_provenance NOT NULL DEFAULT 'ML_PREDICTED',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_renew_run_target UNIQUE (station_id, run_at, target_time)
);
CREATE INDEX ix_renew_station_target ON renewable_forecasts (station_id, target_time);

-- ------------------------------------------ energy_forecasts (AI OUTPUT) --
CREATE TABLE energy_forecasts (
    id                   SERIAL PRIMARY KEY,
    station_id           INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    run_at               TIMESTAMPTZ NOT NULL,
    target_time          TIMESTAMPTZ NOT NULL,
    horizon_h            INTEGER NOT NULL,
    predicted_load_kw    DOUBLE PRECISION NOT NULL,
    load_kw_p10          DOUBLE PRECISION,
    load_kw_p90          DOUBLE PRECISION,
    critical_load_kw     DOUBLE PRECISION DEFAULT 0,
    deferrable_load_kw   DOUBLE PRECISION DEFAULT 0,
    heating_load_kw      DOUBLE PRECISION,
    predicted_energy_kwh DOUBLE PRECISION,
    input_temperature_c  DOUBLE PRECISION,
    input_wind_speed_ms  DOUBLE PRECISION,
    weather_provenance   VARCHAR(32),
    weather_source       VARCHAR(120),
    model_version        VARCHAR(64),
    model_name           VARCHAR(64),
    confidence           DOUBLE PRECISION,
    feature_snapshot     JSONB,
    provenance           data_provenance NOT NULL DEFAULT 'ML_PREDICTED',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_efc_run_target UNIQUE (station_id, run_at, target_time)
);
CREATE INDEX ix_efc_station_target ON energy_forecasts (station_id, target_time);

-- ------------------------------------------------- optimization_results --
CREATE TABLE optimization_results (
    id                      SERIAL PRIMARY KEY,
    station_id              INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    run_at                  TIMESTAMPTZ NOT NULL,
    horizon_h               INTEGER DEFAULT 48,
    objective               VARCHAR(48) DEFAULT 'min_fuel_secure_load',
    strategy                VARCHAR(48) DEFAULT 'receding_horizon',
    baseline_fuel_l         DOUBLE PRECISION DEFAULT 0,
    optimized_fuel_l        DOUBLE PRECISION DEFAULT 0,
    fuel_saved_l            DOUBLE PRECISION DEFAULT 0,
    fuel_saved_pct          DOUBLE PRECISION DEFAULT 0,
    renewable_fraction      DOUBLE PRECISION DEFAULT 0,
    renewable_curtailed_kwh DOUBLE PRECISION DEFAULT 0,
    generator_runtime_h     DOUBLE PRECISION DEFAULT 0,
    generator_starts        INTEGER DEFAULT 0,
    load_shed_kwh           DOUBLE PRECISION DEFAULT 0,
    load_deferred_kwh       DOUBLE PRECISION DEFAULT 0,
    unserved_critical_kwh   DOUBLE PRECISION DEFAULT 0,
    battery_throughput_kwh  DOUBLE PRECISION DEFAULT 0,
    final_soc_pct           DOUBLE PRECISION DEFAULT 0,
    co2_avoided_kg          DOUBLE PRECISION DEFAULT 0,
    feasible                BOOLEAN DEFAULT TRUE,
    solve_ms                DOUBLE PRECISION,
    schedule                JSONB,   -- hour-by-hour dispatch
    rationale               JSONB,   -- human-readable justification
    constraints_binding     JSONB,
    provenance              data_provenance NOT NULL DEFAULT 'OPTIMIZED',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_opt_station_run ON optimization_results (station_id, run_at);

-- ------------------------------------------ crisis_simulations (WHAT-IF) --
CREATE TABLE crisis_simulations (
    id                      SERIAL PRIMARY KEY,
    station_id              INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    run_at                  TIMESTAMPTZ NOT NULL,
    scenario                scenario_type NOT NULL,
    scenario_label          VARCHAR(120) NOT NULL,
    duration_h              INTEGER DEFAULT 72,
    severity                DOUBLE PRECISION DEFAULT 1,
    parameters              JSONB,
    survival_hours          DOUBLE PRECISION DEFAULT 0,
    baseline_survival_hours DOUBLE PRECISION DEFAULT 0,
    survival_delta_hours    DOUBLE PRECISION DEFAULT 0,
    min_soc_pct             DOUBLE PRECISION DEFAULT 0,
    fuel_used_l             DOUBLE PRECISION DEFAULT 0,
    fuel_remaining_l        DOUBLE PRECISION DEFAULT 0,
    load_shed_kwh           DOUBLE PRECISION DEFAULT 0,
    unserved_critical_kwh   DOUBLE PRECISION DEFAULT 0,
    critical_load_secured   BOOLEAN DEFAULT TRUE,
    blackout_occurred       BOOLEAN DEFAULT FALSE,
    time_to_first_shed_h    DOUBLE PRECISION,
    severity_rating         VARCHAR(32),
    timeline                JSONB,
    actions_taken           JSONB,
    summary                 TEXT,
    disclaimer              VARCHAR(200) DEFAULT 'SIMULATED SCENARIO - NOT LIVE STATION TELEMETRY',
    provenance              data_provenance NOT NULL DEFAULT 'SIMULATED',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_crisis_station_run ON crisis_simulations (station_id, run_at);
CREATE INDEX ix_crisis_scenario    ON crisis_simulations (scenario);

-- -------------------------------------------------------- recommendations --
CREATE TABLE recommendations (
    id                        SERIAL PRIMARY KEY,
    station_id                INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    generated_at              TIMESTAMPTZ NOT NULL,
    category                  recommendation_category NOT NULL,
    urgency                   urgency NOT NULL DEFAULT 'ROUTINE',
    title                     VARCHAR(200) NOT NULL,
    action                    TEXT NOT NULL,
    rationale                 TEXT NOT NULL,
    drivers                   JSONB,   -- ranked explainability drivers
    expected_benefit          TEXT,
    estimated_fuel_saving_l   DOUBLE PRECISION,
    estimated_autonomy_gain_h DOUBLE PRECISION,
    confidence                DOUBLE PRECISION,
    evidence                  JSONB,
    counterfactual            TEXT,
    is_active                 BOOLEAN DEFAULT TRUE,
    acted_on                  BOOLEAN DEFAULT FALSE,
    source_module             VARCHAR(64),
    provenance                data_provenance NOT NULL DEFAULT 'ML_PREDICTED',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_rec_station_created ON recommendations (station_id, generated_at);
CREATE INDEX ix_rec_active          ON recommendations (station_id, is_active);
CREATE INDEX ix_rec_urgency         ON recommendations (urgency);

-- ------------------------------------------------------------------ alerts --
CREATE TABLE alerts (
    id                 SERIAL PRIMARY KEY,
    station_id         INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    raised_at          TIMESTAMPTZ NOT NULL,
    resolved_at        TIMESTAMPTZ,
    code               VARCHAR(48) NOT NULL,
    severity           alert_severity NOT NULL,
    status             alert_status NOT NULL DEFAULT 'ACTIVE',
    title              VARCHAR(200) NOT NULL,
    message            TEXT NOT NULL,
    subsystem          VARCHAR(48),
    metric_name        VARCHAR(64),
    metric_value       DOUBLE PRECISION,
    threshold_value    DOUBLE PRECISION,
    recommended_action TEXT,
    detail             JSONB,
    provenance         data_provenance NOT NULL DEFAULT 'MODELLED',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_alert_station_raised ON alerts (station_id, raised_at);
CREATE INDEX ix_alert_status         ON alerts (station_id, status);
CREATE INDEX ix_alert_code           ON alerts (code);
