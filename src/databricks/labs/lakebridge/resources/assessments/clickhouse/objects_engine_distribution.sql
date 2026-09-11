-- Object count and size by engine type (system.tables). Replicated metadata: identical on OSS and Cloud.
SELECT
    database,
    engine,
    count() AS object_count,
    sum(total_rows) AS sum_rows,
    sum(total_bytes) AS sum_bytes,
    formatReadableSize(sum(total_bytes)) AS total_size_readable
FROM system.tables
WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
GROUP BY database, engine
ORDER BY database, object_count DESC
