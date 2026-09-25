-- Daily CPU/memory/concurrency trend (system.metric_log). Optional: metric_log may be disabled.
SELECT
    toStartOfDay(event_time) AS day,
    round(avg(ProfileEvent_OSCPUVirtualTimeMicroseconds) / 1000000, 2) AS avg_cpu_seconds,
    round(max(ProfileEvent_OSCPUVirtualTimeMicroseconds) / 1000000, 2) AS peak_cpu_seconds,
    round(avg(CurrentMetric_MemoryTracking) / (1024*1024*1024), 2) AS avg_memory_gb,
    round(max(CurrentMetric_MemoryTracking) / (1024*1024*1024), 2) AS peak_memory_gb,
    avg(CurrentMetric_Query) AS avg_concurrent_queries,
    max(CurrentMetric_Query) AS peak_concurrent_queries,
    avg(CurrentMetric_Merge) AS avg_concurrent_merges,
    max(CurrentMetric_Merge) AS peak_concurrent_merges
FROM system.metric_log
WHERE event_time >= now() - INTERVAL 30 DAY
GROUP BY day
ORDER BY day
