CREATE TABLE IF NOT EXISTS costs_compute_by_hour (
    hour_of_day BIGINT,
    runs BIGINT,
    compute_weight BIGINT,
    total_duration_ms BIGINT,
    avg_memory_usage DOUBLE
);
