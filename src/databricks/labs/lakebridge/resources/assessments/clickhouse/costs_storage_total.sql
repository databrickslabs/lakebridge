-- Storage grand totals (system.parts). Replicated: identical on OSS and Cloud.
-- Single-row rollup, computed directly from parts (the Python collector summed storage_by_table).
SELECT
    round(sum(data_compressed_bytes) / pow(1024, 3), 6) AS total_compressed_gb,
    round(sum(bytes_on_disk) / pow(1024, 3), 6) AS total_disk_gb
FROM system.parts
WHERE active = 1
  AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
