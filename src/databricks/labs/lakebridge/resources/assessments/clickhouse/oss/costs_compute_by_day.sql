-- Daily compute trend (query_log).
SELECT
    toDate(event_time) AS day,
    count() AS runs,
    sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS compute_weight,
    sum(read_bytes) AS total_read_bytes,
    sum(written_bytes) AS total_written_bytes
FROM system.query_log
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY day
ORDER BY day
