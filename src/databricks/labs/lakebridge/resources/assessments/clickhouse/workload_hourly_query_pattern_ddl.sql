CREATE TABLE IF NOT EXISTS workload_hourly_query_pattern (
    hour_of_day BIGINT,
    query_count BIGINT,
    distinct_users BIGINT,
    avg_duration_ms DOUBLE,
    p95_duration_ms DOUBLE,
    p99_duration_ms DOUBLE,
    total_read_bytes BIGINT
);
