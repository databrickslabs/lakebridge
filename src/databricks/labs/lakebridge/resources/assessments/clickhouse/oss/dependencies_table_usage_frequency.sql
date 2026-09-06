-- Table access frequency (query_log).
-- query_kind_list is an aggregated Array(String), JSON-encoded to a single column.
SELECT
    tbl,
    count() AS access_count,
    uniqExact(user) AS distinct_users,
    uniqExact(query_kind) AS query_kinds,
    sum(read_bytes) AS total_read_bytes,
    toJSONString(groupUniqArray(query_kind)) AS query_kind_list
FROM system.query_log
ARRAY JOIN tables AS tbl
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY tbl
ORDER BY access_count DESC
LIMIT 100
