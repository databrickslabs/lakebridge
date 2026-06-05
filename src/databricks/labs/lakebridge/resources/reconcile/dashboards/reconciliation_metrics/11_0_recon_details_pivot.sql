/* --title 'Recon Details Drill Down' --height 6 --width 6 */
-- Legacy (key, value) drill down for the pivot tile. The reshape from the record-level model
-- lives in the details_kv view; this stays a plain SELECT so the dashboard tile parser handles it.
-- dd_record_key (the join-key values as JSON) separates records in the pivot.
SELECT
    dd_recon_id,
    dd_source_table,
    dd_target_table,
    dd_recon_type,
    dd_record_key,
    key,
    value
FROM remorph.reconcile.details_kv
ORDER BY dd_recon_id, dd_target_table, dd_record_key, key
