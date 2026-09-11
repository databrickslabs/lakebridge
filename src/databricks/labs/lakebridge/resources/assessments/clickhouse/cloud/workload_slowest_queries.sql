-- Slowest individual queries in the window (query_log).
-- query is redacted (default-on). databases/tables are Array(String), JSON-encoded to one column.
SELECT
    event_time,
    user,
    query_kind,
    query_duration_ms,
    read_rows,
    read_bytes,
    written_rows,
    written_bytes,
    memory_usage,
    result_rows,
    toJSONString(databases) AS databases,
    toJSONString(tables) AS tables,
    '[REDACTED]' AS query
FROM clusterAllReplicas('default', system.query_log)
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
ORDER BY query_duration_ms DESC
LIMIT 50
