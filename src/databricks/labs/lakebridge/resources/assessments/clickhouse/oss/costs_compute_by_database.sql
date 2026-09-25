-- Compute attribution by database (query_log, ARRAY JOIN databases).
SELECT
    *,
    round(compute_weight / nullIf(sum(compute_weight) OVER (), 0) * 100, 2) AS compute_weight_pct
FROM (
    SELECT
        db,
        count() AS runs,
        sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS compute_weight,
        sum(query_duration_ms) AS total_duration_ms,
        sum(read_bytes) AS total_read_bytes,
        sum(written_bytes) AS total_written_bytes
    FROM system.query_log
    ARRAY JOIN databases AS db
    WHERE type = 'QueryFinish'
      AND is_initial_query = 1
      AND event_time >= now() - INTERVAL 30 DAY
      AND db NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
    GROUP BY db
)
ORDER BY compute_weight DESC
