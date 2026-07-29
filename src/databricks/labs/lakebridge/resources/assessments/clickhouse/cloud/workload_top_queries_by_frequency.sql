-- Top query shapes by execution count, with latency percentiles (query_log).
-- sample_query is redacted (default-on): raw SQL may embed literal predicate values / PII.
SELECT
    normalized_query_hash,
    any(query_kind) AS query_kind,
    any(user) AS sample_user,
    count() AS executions,
    quantileTDigest(0.5)(query_duration_ms) AS p50_ms,
    quantileTDigest(0.95)(query_duration_ms) AS p95_ms,
    quantileTDigest(0.99)(query_duration_ms) AS p99_ms,
    avg(memory_usage) AS avg_memory,
    max(memory_usage) AS max_memory,
    sum(read_bytes) AS total_read_bytes,
    sum(read_rows) AS total_read_rows,
    '[REDACTED]' AS sample_query
FROM clusterAllReplicas('default', system.query_log)
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY normalized_query_hash
ORDER BY executions DESC
LIMIT 100
