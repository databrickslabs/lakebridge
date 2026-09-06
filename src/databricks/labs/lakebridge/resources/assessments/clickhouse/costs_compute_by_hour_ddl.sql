CREATE TABLE IF NOT EXISTS costs_compute_by_hour (
    hour_of_day BIGINT,
    runs UBIGINT,
    compute_weight UBIGINT,
    total_duration_ms UBIGINT,
    avg_memory_usage DOUBLE
);
