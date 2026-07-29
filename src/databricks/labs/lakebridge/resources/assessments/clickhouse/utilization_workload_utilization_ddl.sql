CREATE TABLE IF NOT EXISTS utilization_workload_utilization (
    hour TIMESTAMP,
    queries_in_hour BIGINT,
    total_query_ms BIGINT,
    query_time_utilization DOUBLE,
    max_threads_used BIGINT,
    avg_threads_used DOUBLE,
    avg_memory_usage DOUBLE,
    max_memory_usage BIGINT
);
