-- Legacy long (key, value) projection of the record-level details, so the original
-- "Recon Details Drill Down" pivot tile keeps working. Each sampled record is identified by
-- dd_record_key (the join-key values as JSON), which the pivot uses to separate records.
-- Re-emits the old map convention from the new VARIANT row images:
--   mismatch              -> join-key columns (plain) + <col>_base / <col>_compare / <col>_match
--   missing_* / threshold -> full row image, one plain key per column
-- The dashboard query stays trivial (SELECT * FROM details_kv) so the lsql tile parser is happy.
CREATE OR REPLACE VIEW details_kv AS
WITH base AS (
    SELECT
        main.recon_id AS dd_recon_id,
        IF(
            main.source_table.catalog IS NULL,
            CONCAT_WS('.', main.source_table.schema, main.source_table.table_name),
            CONCAT_WS('.', main.source_table.catalog, main.source_table.schema, main.source_table.table_name)
        ) AS dd_source_table,
        CONCAT_WS('.', main.target_table.catalog, main.target_table.schema, main.target_table.table_name) AS dd_target_table,
        d.recon_type AS dd_recon_type,
        to_json(d.record_key) AS dd_record_key,
        d.record_key,
        d.source_row,
        d.target_row,
        d.mismatch_columns
    FROM details d
             INNER JOIN main
                        ON main.recon_table_id = d.recon_table_id
)
-- mismatch: join-key columns as plain key/value
SELECT dd_recon_id, dd_source_table, dd_target_table, dd_recon_type, dd_record_key,
       kc.key AS key,
       try_variant_get(record_key, concat('$["', kc.key, '"]'), 'string') AS value
FROM base, LATERAL variant_explode(record_key) AS kc
WHERE dd_recon_type = 'mismatch'
UNION ALL
-- mismatch: <col>_base / <col>_compare / <col>_match for every compared column
SELECT x.dd_recon_id, x.dd_source_table, x.dd_target_table, x.dd_recon_type, x.dd_record_key, kv.key, kv.value
FROM (
    SELECT b.*, sc.key AS col
    FROM base b, LATERAL variant_explode(b.source_row) AS sc
    WHERE b.dd_recon_type = 'mismatch'
) x
LATERAL VIEW stack(3,
    CONCAT(x.col, '_base'),    try_variant_get(x.source_row, CONCAT('$["', x.col, '"]'), 'string'),
    CONCAT(x.col, '_compare'), try_variant_get(x.target_row, CONCAT('$["', x.col, '"]'), 'string'),
    CONCAT(x.col, '_match'),   CAST(NOT array_contains(x.mismatch_columns, x.col) AS string)
) kv AS key, value
UNION ALL
-- missing_in_source / missing_in_target / threshold: full row image, one plain key per column
SELECT dd_recon_id, dd_source_table, dd_target_table, dd_recon_type, dd_record_key,
       vc.key AS key,
       try_variant_get(COALESCE(source_row, target_row), concat('$["', vc.key, '"]'), 'string') AS value
FROM base, LATERAL variant_explode(COALESCE(source_row, target_row)) AS vc
WHERE dd_recon_type <> 'mismatch';
