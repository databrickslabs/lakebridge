-- Resource footprint rollup: storage from system.parts (scalar subqueries), compute/activity
-- from query_log. Reported for both Cloud and OSS; no synthetic dollar estimation.
SELECT
    'self-managed (OSS)' AS deployment,
    (SELECT round(sum(data_compressed_bytes) / pow(1024, 3), 6) FROM system.parts
       WHERE active = 1 AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')) AS total_compressed_gb,
    (SELECT round(sum(bytes_on_disk) / pow(1024, 3), 6) FROM system.parts
       WHERE active = 1 AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')) AS total_disk_gb,
    30 AS analysis_period_days,
    sum(query_duration_ms * greatest(peak_threads_usage, 1)) AS total_compute_weight,
    round(sum(query_duration_ms) / 60000.0, 2) AS total_query_minutes,
    uniqExact(toHour(event_time)) AS active_query_hours_of_day,
    'Resource footprint (disk GB, compute-weight distribution, query activity) plus the per-table/db/user usage attribution. On Cloud the actual billed cost is in costs_pricing_config.actual_billed_cost (when Cloud API credentials are configured); on self-managed / OSS, use this footprint to size against your own infrastructure.' AS note
FROM system.query_log
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
