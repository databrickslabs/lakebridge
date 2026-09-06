CREATE TABLE IF NOT EXISTS costs_top_expensive_queries (
    normalized_query_hash UBIGINT,
    query_kind VARCHAR,
    sample_user VARCHAR,
    runs UBIGINT,
    compute_weight UBIGINT,
    total_read_bytes UBIGINT,
    total_written_bytes UBIGINT,
    max_memory_usage UBIGINT,
    p95_ms DOUBLE,
    p99_ms DOUBLE,
    sample_query VARCHAR
);
