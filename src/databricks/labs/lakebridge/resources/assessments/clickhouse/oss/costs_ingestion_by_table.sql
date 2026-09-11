-- Synchronous insert attribution (query_log, ARRAY JOIN tables).
SELECT
    tbl,
    count() AS insert_count,
    sum(written_bytes) AS total_written_bytes,
    sum(written_rows) AS total_written_rows,
    sum(query_duration_ms) AS total_duration_ms
FROM system.query_log
ARRAY JOIN tables AS tbl
WHERE type = 'QueryFinish'
  AND query_kind = 'Insert'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
  AND NOT startsWith(tbl, 'system.')
  AND NOT startsWith(tbl, 'information_schema.')
GROUP BY tbl
ORDER BY total_written_bytes DESC
