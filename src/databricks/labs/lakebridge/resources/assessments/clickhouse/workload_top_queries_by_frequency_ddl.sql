CREATE TABLE IF NOT EXISTS workload_top_queries_by_frequency (
    normalized_query_hash UBIGINT,
    query_kind VARCHAR,
    sample_user VARCHAR,
    executions BIGINT,
    p50_ms DOUBLE,
    p95_ms DOUBLE,
    p99_ms DOUBLE,
    avg_memory DOUBLE,
    max_memory BIGINT,
    total_read_bytes BIGINT,
    total_read_rows BIGINT,
    sample_query VARCHAR
);
