CREATE TABLE IF NOT EXISTS workload_most_expensive_queries (
    normalized_query_hash UBIGINT,
    runs UBIGINT,
    total_read_bytes UBIGINT,
    avg_read_bytes DOUBLE,
    total_read_rows UBIGINT,
    p95_ms DOUBLE,
    avg_memory DOUBLE,
    sample_query VARCHAR
);
