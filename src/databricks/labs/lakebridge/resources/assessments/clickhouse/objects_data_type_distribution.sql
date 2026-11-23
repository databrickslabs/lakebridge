-- Data type usage distribution (system.columns). Replicated metadata: identical on OSS and Cloud.
SELECT
    type,
    count() AS usage_count,
    uniqExact(database, table) AS distinct_tables,
    sum(data_compressed_bytes) AS total_compressed_bytes,
    sum(data_uncompressed_bytes) AS total_uncompressed_bytes
FROM system.columns
WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
GROUP BY type
ORDER BY usage_count DESC
