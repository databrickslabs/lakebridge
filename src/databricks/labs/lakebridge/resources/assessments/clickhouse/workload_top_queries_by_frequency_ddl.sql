CREATE TABLE IF NOT EXISTS workload_top_queries_by_frequency (
    normalized_query_hash UBIGINT,
    query_kind VARCHAR,
    sample_user VARCHAR,
    executions UBIGINT,
    p50_ms DOUBLE,
    p95_ms DOUBLE,
    p99_ms DOUBLE,
    avg_memory DOUBLE,
    max_memory UBIGINT,
    total_read_bytes UBIGINT,
    total_read_rows UBIGINT,
    sample_query VARCHAR
);
