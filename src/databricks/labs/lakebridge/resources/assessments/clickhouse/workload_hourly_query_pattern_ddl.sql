CREATE TABLE IF NOT EXISTS workload_hourly_query_pattern (
    hour_of_day BIGINT,
    query_count UBIGINT,
    distinct_users UBIGINT,
    avg_duration_ms DOUBLE,
    p95_duration_ms DOUBLE,
    p99_duration_ms DOUBLE,
    total_read_bytes UBIGINT
);
