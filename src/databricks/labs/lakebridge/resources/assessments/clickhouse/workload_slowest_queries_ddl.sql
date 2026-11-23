CREATE TABLE IF NOT EXISTS workload_slowest_queries (
    event_time TIMESTAMP,
    user VARCHAR,
    query_kind VARCHAR,
    query_duration_ms UBIGINT,
    read_rows UBIGINT,
    read_bytes UBIGINT,
    written_rows UBIGINT,
    written_bytes UBIGINT,
    memory_usage UBIGINT,
    result_rows UBIGINT,
    databases VARCHAR,
    tables VARCHAR,
    query VARCHAR
);
