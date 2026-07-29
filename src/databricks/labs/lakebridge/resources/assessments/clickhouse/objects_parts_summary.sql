-- Parts and storage layout per table (system.parts). Replicated metadata: identical on OSS and Cloud.
SELECT
    database,
    table,
    count() AS active_parts,
    sum(rows) AS total_rows,
    sum(bytes_on_disk) AS total_bytes_on_disk,
    formatReadableSize(sum(bytes_on_disk)) AS size_on_disk,
    sum(data_compressed_bytes) AS data_compressed,
    sum(data_uncompressed_bytes) AS data_uncompressed,
    round(sum(data_uncompressed_bytes) / nullIf(sum(data_compressed_bytes), 0), 2) AS compression_ratio,
    min(min_date) AS min_date,
    max(max_date) AS max_date,
    count(DISTINCT partition_id) AS partition_count
FROM system.parts
WHERE active
  AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
GROUP BY database, table
ORDER BY total_bytes_on_disk DESC
