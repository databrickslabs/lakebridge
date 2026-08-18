CREATE TABLE IF NOT EXISTS workload_user_workload_summary (
    user VARCHAR,
    total_queries UBIGINT,
    query_kinds_used UBIGINT,
    distinct_queries UBIGINT,
    total_read_bytes UBIGINT,
    total_written_bytes UBIGINT,
    avg_duration_ms DOUBLE,
    p95_duration_ms DOUBLE,
    p99_duration_ms DOUBLE,
    peak_memory UBIGINT,
    first_seen TIMESTAMP,
    last_seen TIMESTAMP
);
