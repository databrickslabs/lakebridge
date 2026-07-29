CREATE TABLE IF NOT EXISTS workload_error_analysis (
    exception_code BIGINT,
    sample_exception VARCHAR,
    occurrences BIGINT,
    affected_users BIGINT,
    distinct_queries BIGINT,
    first_seen TIMESTAMP,
    last_seen TIMESTAMP
);
