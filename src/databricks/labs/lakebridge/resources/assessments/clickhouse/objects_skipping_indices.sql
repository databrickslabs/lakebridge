-- Data skipping indices (system.data_skipping_indices). Replicated metadata: identical on OSS and Cloud.
SELECT
    database,
    table,
    name AS index_name,
    type AS index_type,
    expr,
    granularity,
    data_compressed_bytes,
    data_uncompressed_bytes
FROM system.data_skipping_indices
WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
ORDER BY database, table
