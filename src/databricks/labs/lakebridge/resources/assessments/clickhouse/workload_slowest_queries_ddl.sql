CREATE TABLE IF NOT EXISTS workload_slowest_queries (
    event_time TIMESTAMP,
    user VARCHAR,
    query_kind VARCHAR,
    query_duration_ms BIGINT,
    read_rows BIGINT,
    read_bytes BIGINT,
    written_rows BIGINT,
    written_bytes BIGINT,
    memory_usage BIGINT,
    result_rows BIGINT,
    databases VARCHAR,
    tables VARCHAR,
    query VARCHAR
);
