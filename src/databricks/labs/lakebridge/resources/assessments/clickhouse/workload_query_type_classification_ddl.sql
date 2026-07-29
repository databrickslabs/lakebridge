CREATE TABLE IF NOT EXISTS workload_query_type_classification (
    query_category VARCHAR,
    query_count BIGINT,
    pct_of_total DOUBLE,
    distinct_users BIGINT,
    total_read_bytes BIGINT,
    avg_duration_ms DOUBLE
);
