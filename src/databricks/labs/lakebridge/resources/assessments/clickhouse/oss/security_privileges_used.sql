-- Privileges exercised by queries (query_log.used_privileges).
SELECT
    priv,
    count() AS query_count,
    uniqExact(user) AS distinct_users
FROM system.query_log
ARRAY JOIN used_privileges AS priv
WHERE type = 'QueryFinish'
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY priv
ORDER BY query_count DESC
LIMIT 50
