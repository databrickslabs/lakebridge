-- Partitioned tables (system.tables). Replicated: identical on OSS and Cloud.
SELECT
    database,
    name,
    engine,
    partition_key,
    sorting_key,
    total_rows,
    total_bytes
FROM system.tables
WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
  AND partition_key != ''
ORDER BY total_bytes DESC
