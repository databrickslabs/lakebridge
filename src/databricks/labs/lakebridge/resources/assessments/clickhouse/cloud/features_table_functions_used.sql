-- Table functions used in queries (query_log.used_table_functions).
SELECT
    tf,
    count() AS query_count,
    uniqExact(user) AS distinct_users
FROM clusterAllReplicas('default', system.query_log)
ARRAY JOIN used_table_functions AS tf
WHERE type = 'QueryFinish'
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY tf
ORDER BY query_count DESC
