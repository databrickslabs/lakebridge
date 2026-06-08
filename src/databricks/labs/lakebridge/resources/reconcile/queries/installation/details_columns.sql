CREATE OR REPLACE VIEW details_columns AS
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
