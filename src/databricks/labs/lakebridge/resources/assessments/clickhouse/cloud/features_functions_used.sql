-- Functions used in queries (query_log.used_functions).
SELECT
    func,
    count() AS query_count,
    uniqExact(user) AS distinct_users
FROM clusterAllReplicas('default', system.query_log)
ARRAY JOIN used_functions AS func
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY func
ORDER BY query_count DESC
LIMIT 100
