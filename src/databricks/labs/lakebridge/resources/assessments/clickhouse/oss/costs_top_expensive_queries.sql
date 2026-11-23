-- Most compute-expensive query shapes (query_log). sample_query redacted (default-on).
SELECT
    normalized_query_hash,
    any(query_kind) AS query_kind,
    any(user) AS sample_user,
    count() AS runs,
    sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS compute_weight,
    sum(read_bytes) AS total_read_bytes,
    sum(written_bytes) AS total_written_bytes,
    max(memory_usage) AS max_memory_usage,
    quantileTDigest(0.95)(query_duration_ms) AS p95_ms,
    quantileTDigest(0.99)(query_duration_ms) AS p99_ms,
    '[REDACTED]' AS sample_query
FROM system.query_log
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY normalized_query_hash
ORDER BY compute_weight DESC
LIMIT 30
