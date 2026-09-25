-- Concurrency pattern by hour of day (query_log).
SELECT
    toHour(event_time) AS hour_of_day,
    count() AS query_count,
    uniqExact(user) AS distinct_users,
    avg(query_duration_ms) AS avg_duration_ms,
    quantileTDigest(0.95)(query_duration_ms) AS p95_duration_ms,
    quantileTDigest(0.99)(query_duration_ms) AS p99_duration_ms,
    sum(read_bytes) AS total_read_bytes
FROM system.query_log
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY hour_of_day
ORDER BY hour_of_day
