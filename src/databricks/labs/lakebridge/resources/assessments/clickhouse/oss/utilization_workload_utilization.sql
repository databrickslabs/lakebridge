-- Hourly workload utilization estimate (query_log).
SELECT
    toStartOfHour(event_time) AS hour,
    count() AS queries_in_hour,
    sum(query_duration_ms) AS total_query_ms,
    round(sum(query_duration_ms) / 3600000.0, 4) AS query_time_utilization,
    max(peak_threads_usage) AS max_threads_used,
    avg(peak_threads_usage) AS avg_threads_used,
    avg(memory_usage) AS avg_memory_usage,
    max(memory_usage) AS max_memory_usage
FROM system.query_log
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY hour
ORDER BY hour
