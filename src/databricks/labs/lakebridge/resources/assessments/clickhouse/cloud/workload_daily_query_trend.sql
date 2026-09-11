-- Daily query volume trend (query_log).
SELECT
    toDate(event_time) AS day,
    count() AS total_queries,
    countIf(type = 'QueryFinish') AS succeeded,
    countIf(type IN ('ExceptionBeforeStart', 'ExceptionWhileProcessing')) AS failed,
    uniqExact(user) AS active_users,
    sum(read_bytes) AS read_bytes,
    sum(written_bytes) AS written_bytes
FROM clusterAllReplicas('default', system.query_log)
WHERE event_time >= now() - INTERVAL 30 DAY
  AND is_initial_query = 1
GROUP BY day
ORDER BY day
