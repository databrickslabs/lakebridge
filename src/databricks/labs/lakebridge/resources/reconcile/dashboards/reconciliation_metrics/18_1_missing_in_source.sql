/* --title 'Missing in Source' --width 3 */
SELECT
    main.recon_id,
    CONCAT(main.target_table.catalog, '.', main.target_table.schema, '.', main.target_table.table_name) AS target_table,
    metrics.recon_metrics.row_comparison.missing_in_source AS missing_in_source,
    main.start_ts AS start_ts
FROM
    remorph.reconcile.main main
        INNER JOIN remorph.reconcile.metrics metrics
                   ON main.recon_table_id = metrics.recon_table_id
ORDER BY
    metrics.inserted_ts DESC,
    main.recon_id,
    main.target_table.table_name
