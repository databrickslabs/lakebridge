-- High-scan / low-result waste indicators (query_log). sample_query redacted (default-on).
SELECT
    normalized_query_hash,
    any(user) AS sample_user,
    count() AS runs,
    sum(read_bytes) AS total_read_bytes,
    sum(read_rows) AS total_read_rows,
    sum(result_rows) AS total_result_rows,
    round(sum(result_rows) / nullIf(sum(read_rows), 0) * 100, 2) AS scan_efficiency_pct,
    sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS compute_weight,
    '[REDACTED]' AS sample_query
FROM system.query_log
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
  AND read_rows > 1000
GROUP BY normalized_query_hash
HAVING scan_efficiency_pct < 1 AND runs >= 2
ORDER BY compute_weight DESC
LIMIT 20
