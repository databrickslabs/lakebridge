-- Queries expensive because they scan a lot (query_log). scan_efficiency_pct = result/read rows,
-- computed in SQL (the Python collector added it post-query). sample_query redacted (default-on).
SELECT
    *,
    round(total_result_rows / greatest(total_read_rows, 1) * 100, 2) AS scan_efficiency_pct
FROM (
    SELECT
        normalized_query_hash,
        count() AS runs,
        sum(read_bytes) AS total_read_bytes,
        avg(read_bytes) AS avg_read_bytes,
        sum(read_rows) AS total_read_rows,
        sum(result_rows) AS total_result_rows,
        quantileTDigest(0.95)(query_duration_ms) AS p95_ms,
        quantileTDigest(0.99)(query_duration_ms) AS p99_ms,
        '[REDACTED]' AS sample_query
    FROM clusterAllReplicas('default', system.query_log)
    WHERE type = 'QueryFinish'
      AND is_initial_query = 1
      AND event_time >= now() - INTERVAL 30 DAY
    GROUP BY normalized_query_hash
    ORDER BY total_read_bytes DESC
    LIMIT 30
)
