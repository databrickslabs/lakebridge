/* --title 'Number of Distinct Recon IDs per Target Table Failed' --width 6 */
SELECT
    main.recon_id AS rec_id,
    CONCAT(main.target_table.catalog, '.', main.target_table.schema, '.', main.target_table.table_name) AS t_table,
    DATE(main.start_ts) AS start_date
FROM
    remorph.reconcile.main main
    INNER JOIN remorph.reconcile.metrics metrics
ON main.recon_table_id = metrics.recon_table_id
WHERE
    metrics.run_metrics.status = FALSE
ORDER BY
    metrics.inserted_ts DESC,
    main.recon_id,
    main.target_table.table_name
