-- Aggregate functions used in queries (query_log.used_aggregate_functions).
SELECT
    af,
    count() AS query_count
FROM clusterAllReplicas('default', system.query_log)
ARRAY JOIN used_aggregate_functions AS af
WHERE type = 'QueryFinish'
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY af
ORDER BY query_count DESC
LIMIT 50
