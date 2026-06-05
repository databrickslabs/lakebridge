/* --title 'Jobs Summary' --width 6 --height 6 */
SELECT
    main.recon_id,
    main.source_type,
    metrics.run_metrics.run_by_user AS executed_by,
    MIN(main.start_ts) AS start_ts,
    COUNT(*) AS tables,
    SUM(IF(metrics.run_metrics.status, 1, 0)) AS succeeded,
    SUM(IF(NOT metrics.run_metrics.status, 1, 0)) AS failed,
    SUM(COALESCE(metrics.recon_metrics.column_comparison.absolute_mismatch, 0)) AS mismatched_rows,
    SUM(COALESCE(metrics.recon_metrics.row_comparison.missing_in_source, 0)
        + COALESCE(metrics.recon_metrics.row_comparison.missing_in_target, 0)) AS missing_rows
FROM
    remorph.reconcile.main main
        INNER JOIN remorph.reconcile.metrics metrics
                   ON main.recon_table_id = metrics.recon_table_id
WHERE
    LOWER(main.report_type) IN ('all', 'data')
GROUP BY main.recon_id, main.source_type, metrics.run_metrics.run_by_user
ORDER BY start_ts DESC
