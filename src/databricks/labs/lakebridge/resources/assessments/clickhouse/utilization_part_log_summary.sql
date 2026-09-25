-- Merge/insert part activity (system.part_log). Optional: part_log may be disabled.
SELECT
    event_type,
    database,
    table,
    count() AS event_count,
    sum(rows) AS total_rows,
    sum(size_in_bytes) AS total_bytes,
    avg(duration_ms) AS avg_duration_ms,
    max(duration_ms) AS max_duration_ms,
    sum(peak_memory_usage) AS total_peak_memory
FROM system.part_log
WHERE event_time >= now() - INTERVAL 30 DAY
  AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
GROUP BY event_type, database, table
ORDER BY event_count DESC
