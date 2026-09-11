-- Table engine usage from metadata (system.tables). Replicated: identical on OSS and Cloud.
SELECT
    engine,
    count() AS table_count,
    sum(total_rows) AS total_rows,
    sum(total_bytes) AS total_bytes
FROM system.tables
WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
GROUP BY engine
ORDER BY table_count DESC
