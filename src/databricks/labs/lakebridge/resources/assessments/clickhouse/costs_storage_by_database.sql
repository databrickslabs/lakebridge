-- Storage attribution by database (system.parts). Replicated: identical on OSS and Cloud.
-- compressed_gb is derived in an outer wrapper to avoid nesting sum() inside sum() (ILLEGAL_AGGREGATION).
SELECT
    *,
    round(compressed_bytes / pow(1024, 3), 6) AS compressed_gb
FROM (
    SELECT
        database,
        sum(bytes_on_disk) AS bytes_on_disk,
        sum(data_compressed_bytes) AS compressed_bytes,
        sum(data_uncompressed_bytes) AS uncompressed_bytes,
        sum(rows) AS total_rows,
        count(DISTINCT table) AS table_count
    FROM system.parts
    WHERE active = 1
      AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
    GROUP BY database
)
ORDER BY bytes_on_disk DESC
