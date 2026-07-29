CREATE TABLE IF NOT EXISTS costs_top_expensive_queries (
    normalized_query_hash UBIGINT,
    query_kind VARCHAR,
    sample_user VARCHAR,
    runs BIGINT,
    compute_weight BIGINT,
    total_read_bytes BIGINT,
    total_written_bytes BIGINT,
    max_memory_usage BIGINT,
    p95_ms DOUBLE,
    p99_ms DOUBLE,
    sample_query VARCHAR
);
