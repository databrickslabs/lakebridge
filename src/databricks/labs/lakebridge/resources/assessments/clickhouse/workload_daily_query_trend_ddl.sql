CREATE TABLE IF NOT EXISTS workload_daily_query_trend (
    day DATE,
    total_queries BIGINT,
    succeeded BIGINT,
    failed BIGINT,
    active_users BIGINT,
    read_bytes BIGINT,
    written_bytes BIGINT
);
