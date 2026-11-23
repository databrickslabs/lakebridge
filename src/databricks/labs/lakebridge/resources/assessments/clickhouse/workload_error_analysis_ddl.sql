CREATE TABLE IF NOT EXISTS workload_error_analysis (
    exception_code BIGINT,
    sample_exception VARCHAR,
    occurrences UBIGINT,
    affected_users UBIGINT,
    distinct_queries UBIGINT,
    first_seen TIMESTAMP,
    last_seen TIMESTAMP
);
