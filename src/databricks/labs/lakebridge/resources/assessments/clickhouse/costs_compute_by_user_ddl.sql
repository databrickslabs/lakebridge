CREATE TABLE IF NOT EXISTS costs_compute_by_user (
    user VARCHAR,
    runs BIGINT,
    compute_weight BIGINT,
    total_duration_ms BIGINT,
    total_read_bytes BIGINT,
    total_written_bytes BIGINT,
    max_memory_usage BIGINT,
    avg_memory_usage DOUBLE,
    compute_weight_pct DOUBLE
);
