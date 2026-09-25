CREATE TABLE IF NOT EXISTS utilization_part_log_summary (
    event_type VARCHAR,
    database VARCHAR,
    "table" VARCHAR,
    event_count UBIGINT,
    total_rows UBIGINT,
    total_bytes UBIGINT,
    avg_duration_ms DOUBLE,
    max_duration_ms UBIGINT,
    total_peak_memory UBIGINT
);
