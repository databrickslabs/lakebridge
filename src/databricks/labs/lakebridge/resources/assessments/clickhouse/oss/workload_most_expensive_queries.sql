-- Most expensive query shapes by bytes scanned (query_log). sample_query redacted (default-on).
SELECT
    normalized_query_hash,
    count() AS runs,
    sum(read_bytes) AS total_read_bytes,
    avg(read_bytes) AS avg_read_bytes,
    sum(read_rows) AS total_read_rows,
    quantileTDigest(0.95)(query_duration_ms) AS p95_ms,
    avg(memory_usage) AS avg_memory,
    '[REDACTED]' AS sample_query
FROM system.query_log
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY normalized_query_hash
ORDER BY total_read_bytes DESC
LIMIT 50
