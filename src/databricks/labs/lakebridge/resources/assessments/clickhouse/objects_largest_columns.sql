-- Largest columns by compressed footprint (system.parts_columns, for Cloud compat). Replicated
-- metadata: identical on OSS and Cloud.
SELECT
    database,
    table,
    column AS column_name,
    type,
    sum(column_data_compressed_bytes) AS compressed_bytes,
    sum(column_data_uncompressed_bytes) AS uncompressed_bytes,
    formatReadableSize(sum(column_data_compressed_bytes)) AS compressed_readable,
    round(sum(column_data_uncompressed_bytes) / nullIf(sum(column_data_compressed_bytes), 0), 2) AS compression_ratio
FROM system.parts_columns
WHERE active
  AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
GROUP BY database, table, column, type
HAVING compressed_bytes > 0
ORDER BY compressed_bytes DESC
LIMIT 100
