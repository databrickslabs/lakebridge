-- Storage types used in queries (query_log.used_storages).
SELECT
    storage,
    count() AS query_count,
    uniqExact(user) AS distinct_users
FROM system.query_log
ARRAY JOIN used_storages AS storage
WHERE type = 'QueryFinish'
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY storage
ORDER BY query_count DESC
