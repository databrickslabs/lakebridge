CREATE TABLE IF NOT EXISTS workload_most_expensive_queries (
    normalized_query_hash UBIGINT,
    runs BIGINT,
    total_read_bytes BIGINT,
    avg_read_bytes DOUBLE,
    total_read_rows BIGINT,
    p95_ms DOUBLE,
    avg_memory DOUBLE,
    sample_query VARCHAR
);
