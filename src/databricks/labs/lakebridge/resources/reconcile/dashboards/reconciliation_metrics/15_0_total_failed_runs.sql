/* --title 'Total number of runs failed' --width 2 */
SELECT
    main.recon_id AS rec_id,
    DATE(main.start_ts) AS start_date
FROM
    remorph.reconcile.main main
    INNER JOIN remorph.reconcile.metrics metrics
ON main.recon_table_id = metrics.recon_table_id
WHERE
    metrics.run_metrics.status = FALSE
