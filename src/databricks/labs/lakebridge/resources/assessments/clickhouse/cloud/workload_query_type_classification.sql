-- Business-oriented query classification (query_log).
SELECT
    multiIf(
        query_kind = 'Select', 'BI Query / Analytics',
        query_kind = 'Insert', 'Data Ingestion',
        query_kind IN ('Create', 'Alter', 'Drop', 'Rename'), 'DDL',
        query_kind IN ('Grant', 'Revoke'), 'Security',
        query_kind = 'Optimize', 'Maintenance',
        query_kind = 'System', 'System',
        query_kind IN ('Delete', 'Update'), 'DML / Transform',
        query_kind = 'Explain', 'Explain',
        'Other'
    ) AS query_category,
    count() AS query_count,
    round(count() * 100.0 / sum(count()) OVER (), 2) AS pct_of_total,
    uniqExact(user) AS distinct_users,
    sum(read_bytes) AS total_read_bytes,
    avg(query_duration_ms) AS avg_duration_ms
FROM clusterAllReplicas('default', system.query_log)
WHERE type = 'QueryFinish'
  AND is_initial_query = 1
  AND event_time >= now() - INTERVAL 30 DAY
GROUP BY query_category
ORDER BY query_count DESC
