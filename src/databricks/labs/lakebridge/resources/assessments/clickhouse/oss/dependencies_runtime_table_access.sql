-- Runtime table access map (query_log).
-- accessed_databases / accessed_tables are aggregated Array(String), JSON-encoded to single columns.
SELECT
    user,
    normalized_query_hash,
    any(query_kind) AS query_kind,
    toJSONString(groupUniqArrayArray(databases)) AS accessed_databases,
    toJSONString(groupUniqArrayArray(tables)) AS accessed_tables,
    count() AS runs,
    sum(read_bytes) AS total_read_bytes
FROM system.query_log
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
  AND length(tables) > 0
GROUP BY user, normalized_query_hash
ORDER BY runs DESC
LIMIT 200
