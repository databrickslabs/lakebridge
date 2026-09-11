-- Compute attribution by query kind (query_log).
SELECT
    query_kind,
    count() AS runs,
    sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS compute_weight,
    sum(query_duration_ms) AS total_duration_ms,
    sum(read_bytes) AS total_read_bytes,
    sum(written_bytes) AS total_written_bytes,
    avg(memory_usage) AS avg_memory_usage
FROM system.query_log
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY query_kind
ORDER BY compute_weight DESC
