/* --title 'Latest Run per Table' --width 6 --height 6 */
WITH ranked AS (
    SELECT
        main.recon_id,
        main.recon_table_id,
        main.source_type,
        CONCAT_WS('.', main.target_table.catalog, main.target_table.schema, main.target_table.table_name) AS target_table,
        IF(
            ISNULL(main.source_table.catalog),
            CONCAT_WS('.', main.source_table.schema, main.source_table.table_name),
            CONCAT_WS('.', main.source_table.catalog, main.source_table.schema, main.source_table.table_name)
        ) AS source_table,
        metrics.run_metrics.run_by_user AS executed_by,
        main.start_ts,
        IF(metrics.run_metrics.status, 'PASSING', 'FAILING') AS status_label,
        COALESCE(metrics.recon_metrics.column_comparison.absolute_mismatch, 0) AS mismatched_rows,
        COALESCE(metrics.recon_metrics.row_comparison.missing_in_source, 0) AS missing_in_source,
        COALESCE(metrics.recon_metrics.row_comparison.missing_in_target, 0) AS missing_in_target,
        ROW_NUMBER() OVER (
            PARTITION BY CONCAT_WS('.', main.target_table.catalog, main.target_table.schema, main.target_table.table_name)
            ORDER BY main.start_ts DESC
        ) AS rn
    FROM
        remorph.reconcile.main main
            INNER JOIN remorph.reconcile.metrics metrics
                       ON main.recon_table_id = metrics.recon_table_id
    WHERE
        LOWER(main.report_type) IN ('all', 'data')
),
failing_cols_per_run AS (
    SELECT recon_table_id, ARRAY_JOIN(COLLECT_SET(column_name), ', ') AS failing_columns
    FROM remorph.reconcile.details_columns
    WHERE is_mismatch
    GROUP BY recon_table_id
)
SELECT
    r.recon_id,
    r.source_type,
    r.target_table,
    r.source_table,
    r.executed_by,
    r.start_ts,
    r.status_label,
    r.mismatched_rows,
    r.missing_in_source,
    r.missing_in_target,
    COALESCE(fc.failing_columns, '') AS failing_columns
FROM ranked r
    LEFT JOIN failing_cols_per_run fc ON r.recon_table_id = fc.recon_table_id
WHERE r.rn = 1
ORDER BY r.target_table
