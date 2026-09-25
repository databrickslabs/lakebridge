CREATE TABLE IF NOT EXISTS utilization_metric_log_trend (
    hour TIMESTAMP,
    avg_running_queries DOUBLE,
    max_running_queries BIGINT,
    avg_running_merges DOUBLE,
    avg_memory_tracking DOUBLE,
    max_memory_tracking BIGINT
);
