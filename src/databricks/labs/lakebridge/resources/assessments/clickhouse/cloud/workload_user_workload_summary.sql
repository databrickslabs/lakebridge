-- Per-user workload summary (query_log).
SELECT
    user,
    count() AS total_queries,
    uniqExact(query_kind) AS query_kinds_used,
    uniqExact(normalized_query_hash) AS distinct_queries,
    sum(read_bytes) AS total_read_bytes,
    sum(written_bytes) AS total_written_bytes,
    avg(query_duration_ms) AS avg_duration_ms,
    quantileTDigest(0.95)(query_duration_ms) AS p95_duration_ms,
    quantileTDigest(0.99)(query_duration_ms) AS p99_duration_ms,
    max(memory_usage) AS peak_memory,
    min(event_time) AS first_seen,
    max(event_time) AS last_seen
FROM clusterAllReplicas('default', system.query_log)
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY user
ORDER BY total_queries DESC
