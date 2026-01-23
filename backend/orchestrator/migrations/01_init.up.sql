-- GPU Orchestrator Database Schema
-- This file initializes the PostgreSQL database with necessary tables

-- Jobs table: Tracks all submitted jobs
CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY,
    user_id UUID,
    app_id VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,  -- PENDING, QUEUED, PROCESSING, COMPLETED, FAILED
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    worker_id VARCHAR(50),
    cost_estimate DECIMAL(10, 4),
    error_log TEXT,
    params JSONB,  -- Store job parameters as JSON
    output JSONB   -- Store job output/results as JSON
);

-- Create index for faster status lookups
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- GPU metrics table: Time-series data for monitoring
CREATE TABLE IF NOT EXISTS gpu_metrics (
    time TIMESTAMPTZ NOT NULL,
    worker_id VARCHAR(50) NOT NULL,
    gpu_id INTEGER,
    gpu_utilization DOUBLE PRECISION,
    vram_used_mb INTEGER,
    vram_total_mb INTEGER,
    temperature_c INTEGER,
    power_draw_w INTEGER
);

-- Convert to hypertable for time-series optimization
SELECT create_hypertable('gpu_metrics', 'time', if_not_exists => TRUE);

-- Add retention policy: keep metrics for 30 days
SELECT add_retention_policy('gpu_metrics', INTERVAL '30 days', if_not_exists => TRUE);

-- Worker registry table: Track active workers
CREATE TABLE IF NOT EXISTS workers (
    worker_id VARCHAR(50) PRIMARY KEY,
    hostname VARCHAR(255),
    gpu_name VARCHAR(100),
    gpu_memory_total_mb INTEGER,
    status VARCHAR(20),  -- ONLINE, OFFLINE, BUSY, MAINTENANCE
    last_heartbeat TIMESTAMPTZ,
    registered_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create function to update last_heartbeat
CREATE OR REPLACE FUNCTION update_worker_heartbeat(p_worker_id VARCHAR)
RETURNS VOID AS $$
BEGIN
    UPDATE workers
    SET last_heartbeat = NOW(), status = 'ONLINE'
    WHERE worker_id = p_worker_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON TABLE jobs IS 'Stores all job submissions and their lifecycle status';
COMMENT ON TABLE gpu_metrics IS 'Time-series GPU performance metrics from workers';
COMMENT ON TABLE workers IS 'Registry of all workers and their health status';
