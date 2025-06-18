/* --title 'Unique target tables failed' --width 2 */
SELECT
    CONCAT_WS('.', main.target_table.catalog, main.target_table.schema, main.target_table.table_name) AS t_table,
    DATE(main.start_ts) AS start_date
FROM
    remorph.reconcile.main main
    INNER JOIN remorph.reconcile.metrics metrics
ON main.recon_table_id = metrics.recon_table_id
WHERE
    metrics.run_metrics.status = FALSE
