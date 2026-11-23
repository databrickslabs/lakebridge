-- Column access frequency (query_log.columns).
SELECT
    col,
    count() AS access_count,
    uniqExact(user) AS distinct_users
FROM clusterAllReplicas('default', system.query_log)
ARRAY JOIN columns AS col
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY col
ORDER BY access_count DESC
LIMIT 200
