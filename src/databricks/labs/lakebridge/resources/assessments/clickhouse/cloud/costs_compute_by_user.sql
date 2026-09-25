-- Compute attribution by user (query_log). compute_weight_pct computed via window over the
-- per-user aggregate (the Python collector added it post-query).
SELECT
    *,
    round(compute_weight / nullIf(sum(compute_weight) OVER (), 0) * 100, 2) AS compute_weight_pct
FROM (
    SELECT
        user,
        count() AS runs,
        sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS compute_weight,
        sum(query_duration_ms) AS total_duration_ms,
        sum(read_bytes) AS total_read_bytes,
        sum(written_bytes) AS total_written_bytes,
        max(memory_usage) AS max_memory_usage,
        avg(memory_usage) AS avg_memory_usage
    FROM clusterAllReplicas('default', system.query_log)
    WHERE type = 'QueryFinish'
      AND is_initial_query = 1
      AND event_time >= now() - INTERVAL 30 DAY
    GROUP BY user
)
ORDER BY compute_weight DESC
