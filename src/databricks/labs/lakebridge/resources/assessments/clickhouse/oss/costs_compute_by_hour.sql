-- Hourly compute pattern (query_log).
SELECT
    toHour(event_time) AS hour_of_day,
    count() AS runs,
    sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS compute_weight,
    sum(query_duration_ms) AS total_duration_ms,
    avg(memory_usage) AS avg_memory_usage
FROM system.query_log
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY hour_of_day
ORDER BY hour_of_day
