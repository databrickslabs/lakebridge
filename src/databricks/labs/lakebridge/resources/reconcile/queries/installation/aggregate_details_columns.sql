CREATE OR REPLACE VIEW aggregate_details_columns (
    recon_table_id COMMENT 'Join key to main.',
    rule_id COMMENT 'Join key to aggregate_rules.',
    recon_type COMMENT 'mismatch | missing_in_source | missing_in_target.',
    record_key COMMENT 'Group-by values identifying the sampled aggregate group.',
    column_name COMMENT 'Aggregate column name (one row per group-column).',
    source_value COMMENT 'Source aggregate value as string; null if absent.',
    target_value COMMENT 'Target aggregate value as string; null if absent.',
    is_mismatch COMMENT 'True if this aggregate column differs for this group.',
    inserted_ts COMMENT 'Row insert timestamp carried from aggregate_details.'
)
COMMENT 'Exploded per-column view of aggregate_details: one row per (sampled aggregate group, column). Join main on recon_table_id, aggregate_rules on rule_id.'
AS
SELECT
    d.recon_table_id,
    d.rule_id,
    d.recon_type,
    d.record_key,
    kv.key AS column_name,
    try_variant_get(d.source_row, concat('$["', kv.key, '"]'), 'string') AS source_value,
    try_variant_get(d.target_row, concat('$["', kv.key, '"]'), 'string') AS target_value,
    array_contains(d.mismatch_columns, kv.key) AS is_mismatch,
    d.inserted_ts
FROM aggregate_details d,
     LATERAL variant_explode(coalesce(d.source_row, d.target_row)) AS kv;
