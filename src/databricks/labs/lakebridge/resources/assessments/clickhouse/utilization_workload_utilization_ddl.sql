CREATE TABLE IF NOT EXISTS utilization_workload_utilization (
    hour TIMESTAMP,
    queries_in_hour UBIGINT,
    total_query_ms UBIGINT,
    query_time_utilization DOUBLE,
    max_threads_used UBIGINT,
    avg_threads_used DOUBLE,
    avg_memory_usage DOUBLE,
    max_memory_usage UBIGINT
);
