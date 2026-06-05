/* --title 'Total Runs' --width 2 --height 3 */
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
    main.start_ts
FROM
    remorph.reconcile.main main
        INNER JOIN remorph.reconcile.metrics metrics
                   ON main.recon_table_id = metrics.recon_table_id
WHERE
    LOWER(main.report_type) IN ('all', 'data')
