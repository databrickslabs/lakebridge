CREATE TABLE IF NOT EXISTS costs_compute_by_query_type (
    query_kind VARCHAR,
    runs BIGINT,
    compute_weight BIGINT,
    total_duration_ms BIGINT,
    total_read_bytes BIGINT,
    total_written_bytes BIGINT,
    avg_memory_usage DOUBLE
);
