CREATE TABLE IF NOT EXISTS utilization_resource_utilization_trend (
    day TIMESTAMP,
    avg_cpu_seconds DOUBLE,
    peak_cpu_seconds DOUBLE,
    avg_memory_gb DOUBLE,
    peak_memory_gb DOUBLE,
    avg_concurrent_queries DOUBLE,
    peak_concurrent_queries BIGINT,
    avg_concurrent_merges DOUBLE,
    peak_concurrent_merges BIGINT
);
