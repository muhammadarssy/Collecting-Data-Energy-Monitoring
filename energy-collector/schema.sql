-- Schema PostgreSQL untuk Energy Meter Collection System.
-- Identifier kolom sengaja TIDAK di-quote → PostgreSQL menyimpannya lowercase,
-- konsisten dengan INSERT yang dibangun aplikasi (juga tanpa quote).

CREATE TABLE IF NOT EXISTS meter_readings (
    time                  TIMESTAMPTZ     NOT NULL,
    session_id            UUID            NOT NULL,
    cycle_id              UUID            NOT NULL,
    meter_id              TEXT            NOT NULL,
    -- Voltage L-L
    Uab                   DOUBLE PRECISION,
    Ubc                   DOUBLE PRECISION,
    Uca                   DOUBLE PRECISION,
    -- Voltage L-N
    Ua                    DOUBLE PRECISION,
    Ub                    DOUBLE PRECISION,
    Uc                    DOUBLE PRECISION,
    -- Current
    Ia                    DOUBLE PRECISION,
    Ib                    DOUBLE PRECISION,
    Ic                    DOUBLE PRECISION,
    -- Active Power
    Pt                    DOUBLE PRECISION,
    Pa                    DOUBLE PRECISION,
    Pb                    DOUBLE PRECISION,
    Pc                    DOUBLE PRECISION,
    -- Reactive Power
    Qt                    DOUBLE PRECISION,
    Qa                    DOUBLE PRECISION,
    Qb                    DOUBLE PRECISION,
    Qc                    DOUBLE PRECISION,
    -- Power Factor
    PFt                   DOUBLE PRECISION,
    PFa                   DOUBLE PRECISION,
    PFb                   DOUBLE PRECISION,
    PFc                   DOUBLE PRECISION,
    -- Frequency & Demand
    frequency             DOUBLE PRECISION,
    active_power_demand   DOUBLE PRECISION,
    -- Active Energy
    ImpEp                 DOUBLE PRECISION,
    ExpEp                 DOUBLE PRECISION,
    -- Reactive Energy
    Q1Eq                  DOUBLE PRECISION,
    Q2Eq                  DOUBLE PRECISION,
    Q3Eq                  DOUBLE PRECISION,
    Q4Eq                  DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_readings_meter_cycle_time
    ON meter_readings (meter_id, cycle_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_readings_session_time
    ON meter_readings (session_id, time DESC);

-- Tracking sesi produksi
CREATE TABLE IF NOT EXISTS production_sessions (
    session_id    UUID PRIMARY KEY,
    meter_id      TEXT NOT NULL,
    start_time    TIMESTAMPTZ NOT NULL,
    end_time      TIMESTAMPTZ
);

-- Tracking cycle per sesi
CREATE TABLE IF NOT EXISTS production_cycles (
    cycle_id      UUID PRIMARY KEY,
    session_id    UUID NOT NULL,
    meter_id      TEXT NOT NULL,
    start_time    TIMESTAMPTZ NOT NULL,
    end_time      TIMESTAMPTZ,
    -- Delta energi cycle: pembacaan akhir − pembacaan awal (dari meter_readings)
    ImpEp         DOUBLE PRECISION,
    ExpEp         DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_cycles_session ON production_cycles (session_id);

-- Migrasi: tambah kolom energi pada DB yang sudah ada
ALTER TABLE production_cycles ADD COLUMN IF NOT EXISTS ImpEp DOUBLE PRECISION;
ALTER TABLE production_cycles ADD COLUMN IF NOT EXISTS ExpEp DOUBLE PRECISION;
