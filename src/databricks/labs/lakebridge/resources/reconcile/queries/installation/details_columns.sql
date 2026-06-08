CREATE OR REPLACE VIEW details_columns (
    recon_table_id COMMENT 'Join key to main.',
    recon_type COMMENT 'mismatch | missing_in_source | missing_in_target | threshold_mismatch.',
    record_key COMMENT 'Join-column values identifying the sampled record.',
    column_name COMMENT 'Compared column name (one row per record-column).',
    source_value COMMENT 'Source value as string; null if absent on the source side.',
    target_value COMMENT 'Target value as string; null if absent on the target side.',
    is_mismatch COMMENT 'True if this column differs for this record.',
    inserted_ts COMMENT 'Row insert timestamp carried from details.'
)
COMMENT 'Exploded per-column view of details: one row per (sampled record, column). Filter recon_type = mismatch and is_mismatch for the differing columns. Join to main on recon_table_id.'
AS
SELECT
    d.recon_table_id,
    d.recon_type,
    d.record_key,
    kv.key AS column_name,
    try_variant_get(d.source_row, concat('$["', kv.key, '"]'), 'string') AS source_value,
    try_variant_get(d.target_row, concat('$["', kv.key, '"]'), 'string') AS target_value,
    array_contains(d.mismatch_columns, kv.key) AS is_mismatch,
    d.inserted_ts
FROM details d,
     LATERAL variant_explode(coalesce(d.source_row, d.target_row)) AS kv;
