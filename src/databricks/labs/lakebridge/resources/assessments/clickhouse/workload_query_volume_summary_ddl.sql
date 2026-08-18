CREATE TABLE IF NOT EXISTS workload_query_volume_summary (
    query_kind VARCHAR,
    total_queries UBIGINT,
    succeeded UBIGINT,
    failed UBIGINT,
    error_rate_pct DOUBLE,
    distinct_users UBIGINT,
    distinct_query_shapes UBIGINT,
    total_read_bytes UBIGINT,
    total_written_bytes UBIGINT,
    total_read_rows UBIGINT,
    total_written_rows UBIGINT,
    total_result_rows UBIGINT
);
