/* --title 'Mismatched Records' --width 6 */
SELECT
    main.recon_id,
    CONCAT_WS(
            '.',
            main.target_table.`catalog`,
            main.target_table.`schema`,
            main.target_table.table_name
    ) AS target_table,
    main.start_ts,
    metrics.recon_metrics.mismatch AS mismatch
FROM
    remorph.reconcile.main main
        INNER JOIN remorph.reconcile.aggregate_metrics metrics
                   ON main.recon_table_id = metrics.recon_table_id
ORDER BY
    metrics.inserted_ts DESC,
    main.recon_id,
    main.target_table.table_name
