/* --title 'Run Details' --width 6 --height 6 */
SELECT
    main.recon_id,
    main.source_type,
    CONCAT_WS('.', main.target_table.catalog, main.target_table.schema, main.target_table.table_name) AS target_table,
    IF(
        ISNULL(main.source_table.catalog),
        CONCAT_WS('.', main.source_table.schema, main.source_table.table_name),
        CONCAT_WS('.', main.source_table.catalog, main.source_table.schema, main.source_table.table_name)
    ) AS source_table,
    metrics.run_metrics.run_by_user AS executed_by,
    main.start_ts,
    main.end_ts,
    IF(metrics.run_metrics.status, 'PASSING', 'FAILING') AS status_label,
    COALESCE(metrics.recon_metrics.column_comparison.absolute_mismatch, 0) AS mismatched_rows,
    COALESCE(metrics.recon_metrics.row_comparison.missing_in_source, 0) AS missing_in_source,
    COALESCE(metrics.recon_metrics.row_comparison.missing_in_target, 0) AS missing_in_target,
    COALESCE(fc.failing_columns, '') AS failing_columns
FROM
    remorph.reconcile.main main
        INNER JOIN remorph.reconcile.metrics metrics
                   ON main.recon_table_id = metrics.recon_table_id
        LEFT JOIN (
            SELECT recon_table_id, ARRAY_JOIN(COLLECT_SET(column_name), ', ') AS failing_columns
            FROM remorph.reconcile.details_columns
            WHERE is_mismatch
            GROUP BY recon_table_id
        ) fc ON main.recon_table_id = fc.recon_table_id
WHERE
    LOWER(main.report_type) IN ('all', 'data')
ORDER BY main.start_ts DESC, target_table
