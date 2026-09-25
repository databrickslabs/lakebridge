-- Storage attribution by table (system.parts). Replicated: identical on OSS and Cloud.
-- GB columns are derived in an outer wrapper: computing them inline would reference the aggregate
-- aliases (bytes_on_disk / compressed_bytes) and nest sum() inside sum() (ILLEGAL_AGGREGATION).
SELECT
    *,
    round(compressed_bytes / pow(1024, 3), 6) AS compressed_gb,
    round(bytes_on_disk / pow(1024, 3), 6) AS disk_gb
FROM (
    SELECT
        database,
        table,
        sum(bytes_on_disk) AS bytes_on_disk,
        sum(data_compressed_bytes) AS compressed_bytes,
        sum(data_uncompressed_bytes) AS uncompressed_bytes,
        sum(rows) AS rows,
        round(sum(data_uncompressed_bytes) / nullIf(sum(data_compressed_bytes), 0), 2) AS compression_ratio,
        count() AS active_parts
    FROM system.parts
    WHERE active = 1
      AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
    GROUP BY database, table
)
ORDER BY bytes_on_disk DESC
