CREATE TABLE IF NOT EXISTS workload_user_workload_summary (
    user VARCHAR,
    total_queries BIGINT,
    query_kinds_used BIGINT,
    distinct_queries BIGINT,
    total_read_bytes BIGINT,
    total_written_bytes BIGINT,
    avg_duration_ms DOUBLE,
    p95_duration_ms DOUBLE,
    p99_duration_ms DOUBLE,
    peak_memory BIGINT,
    first_seen TIMESTAMP,
    last_seen TIMESTAMP
);
