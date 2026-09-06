-- Data formats used in queries (query_log.used_formats).
SELECT
    fmt,
    count() AS query_count
FROM clusterAllReplicas('default', system.query_log)
ARRAY JOIN used_formats AS fmt
WHERE type = 'QueryFinish'
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY fmt
ORDER BY query_count DESC
