-- View execution lineage (query_views_log). Optional: query_views_log may be disabled.
-- view_query redacted (default-on): view SQL may embed literal values / business logic.
SELECT
    view_name,
    view_type,
    view_target,
    '[REDACTED]' AS view_query,
    count() AS executions,
    sum(read_rows) AS total_read_rows,
    sum(read_bytes) AS total_read_bytes,
    sum(written_rows) AS total_written_rows,
    sum(written_bytes) AS total_written_bytes,
    avg(view_duration_ms) AS avg_duration_ms,
    max(peak_memory_usage) AS max_memory
FROM clusterAllReplicas('default', system.query_views_log)
WHERE event_time >= now() - INTERVAL 30 DAY
GROUP BY view_name, view_type, view_target
ORDER BY executions DESC
