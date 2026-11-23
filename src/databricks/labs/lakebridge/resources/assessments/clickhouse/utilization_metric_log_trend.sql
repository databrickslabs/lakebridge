-- Hourly metric_log trend (system.metric_log). Optional: metric_log may be disabled.
SELECT
    toStartOfHour(event_time) AS hour,
    avg(CurrentMetric_Query) AS avg_running_queries,
    max(CurrentMetric_Query) AS max_running_queries,
    avg(CurrentMetric_Merge) AS avg_running_merges,
    avg(CurrentMetric_MemoryTracking) AS avg_memory_tracking,
    max(CurrentMetric_MemoryTracking) AS max_memory_tracking
FROM system.metric_log
GROUP BY hour
ORDER BY hour
