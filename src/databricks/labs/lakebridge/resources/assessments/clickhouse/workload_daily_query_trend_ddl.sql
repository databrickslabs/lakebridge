CREATE TABLE IF NOT EXISTS workload_daily_query_trend (
    day DATE,
    total_queries UBIGINT,
    succeeded UBIGINT,
    failed UBIGINT,
    active_users UBIGINT,
    read_bytes UBIGINT,
    written_bytes UBIGINT
);
