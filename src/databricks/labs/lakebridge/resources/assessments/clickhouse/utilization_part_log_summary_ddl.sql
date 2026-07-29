CREATE TABLE IF NOT EXISTS utilization_part_log_summary (
    event_type VARCHAR,
    database VARCHAR,
    "table" VARCHAR,
    event_count BIGINT,
    total_rows BIGINT,
    total_bytes BIGINT,
    avg_duration_ms DOUBLE,
    max_duration_ms BIGINT,
    total_peak_memory BIGINT
);
