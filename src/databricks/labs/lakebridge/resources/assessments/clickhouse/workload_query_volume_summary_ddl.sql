CREATE TABLE IF NOT EXISTS workload_query_volume_summary (
    query_kind VARCHAR,
    total_queries BIGINT,
    succeeded BIGINT,
    failed BIGINT,
    error_rate_pct DOUBLE,
    distinct_users BIGINT,
    distinct_query_shapes BIGINT,
    total_read_bytes BIGINT,
    total_written_bytes BIGINT,
    total_read_rows BIGINT,
    total_written_rows BIGINT,
    total_result_rows BIGINT
);
