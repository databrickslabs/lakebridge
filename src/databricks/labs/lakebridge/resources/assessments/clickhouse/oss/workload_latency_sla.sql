-- Latency SLA percentiles by query kind (query_log).
SELECT
    query_kind,
    count() AS runs,
    quantileTDigest(0.5)(query_duration_ms) AS p50_ms,
    quantileTDigest(0.75)(query_duration_ms) AS p75_ms,
    quantileTDigest(0.95)(query_duration_ms) AS p95_ms,
    quantileTDigest(0.99)(query_duration_ms) AS p99_ms,
    max(query_duration_ms) AS max_ms
FROM system.query_log
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY query_kind
ORDER BY runs DESC
