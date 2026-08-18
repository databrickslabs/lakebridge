CREATE TABLE IF NOT EXISTS workload_query_type_classification (
    query_category VARCHAR,
    query_count UBIGINT,
    pct_of_total DOUBLE,
    distinct_users UBIGINT,
    total_read_bytes UBIGINT,
    avg_duration_ms DOUBLE
);
