CREATE TABLE IF NOT EXISTS costs_compute_by_user (
    user VARCHAR,
    runs UBIGINT,
    compute_weight UBIGINT,
    total_duration_ms UBIGINT,
    total_read_bytes UBIGINT,
    total_written_bytes UBIGINT,
    max_memory_usage UBIGINT,
    avg_memory_usage DOUBLE,
    compute_weight_pct DOUBLE
);
