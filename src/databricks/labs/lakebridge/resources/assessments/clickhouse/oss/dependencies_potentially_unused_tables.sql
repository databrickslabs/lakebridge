-- Tables with no query access in the window (query_log LEFT JOIN system.tables).
-- Split because it reads query_log; optional because query_log may be unavailable.
WITH accessed AS (
    SELECT DISTINCT arrayJoin(tables) AS tbl
    FROM system.query_log
    WHERE type = 'QueryFinish'
      AND event_time >= now() - INTERVAL 30 DAY
)
SELECT
    t.database,
    t.name,
    t.engine,
    t.total_rows,
    t.total_bytes,
    formatReadableSize(t.total_bytes) AS size_readable
FROM system.tables t
LEFT JOIN accessed a ON concat(t.database, '.', t.name) = a.tbl
WHERE t.database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
  AND a.tbl IS NULL
  AND t.total_rows > 0
ORDER BY t.total_bytes DESC
