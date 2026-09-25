-- Column inventory with types and sizes (system.columns). Replicated metadata: identical on OSS and Cloud.
-- default_expression is redacted (default-on): a DEFAULT / MATERIALIZED expression may embed literal
-- values or secrets.
SELECT
    database,
    table,
    name AS column_name,
    type,
    default_kind,
    '[REDACTED]' AS default_expression,
    data_compressed_bytes,
    data_uncompressed_bytes,
    marks_bytes,
    comment,
    is_in_partition_key,
    is_in_sorting_key,
    is_in_primary_key,
    is_in_sampling_key
FROM system.columns
WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
ORDER BY database, table, position
