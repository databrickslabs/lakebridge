CREATE TABLE IF NOT EXISTS costs_compute_by_query_type (
    query_kind VARCHAR,
    runs UBIGINT,
    compute_weight UBIGINT,
    total_duration_ms UBIGINT,
    total_read_bytes UBIGINT,
    total_written_bytes UBIGINT,
    avg_memory_usage DOUBLE
);
