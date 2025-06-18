/* --title 'Summary Table' --width 6 --height 6 */
SELECT main.recon_id,
       main.source_type,
       main.report_type,
       main.source_table.`catalog` AS source_catalog,
       main.source_table.`schema` AS source_schema,
       main.source_table.table_name AS source_table_name,
       IF(
           ISNULL(source_catalog),
           CONCAT_WS('.', source_schema, source_table_name),
           CONCAT_WS(
               '.',
               source_catalog,
               source_schema,
               source_table_name
           )
       ) AS source_table,
       main.target_table.`catalog` AS target_catalog,
       main.target_table.`schema` AS target_schema,
       main.target_table.table_name AS target_table_name,
       CONCAT(main.target_table.catalog, '.', main.target_table.schema, '.', main.target_table.table_name) AS target_table,
       metrics.run_metrics.status AS status,
       metrics.run_metrics.exception_message AS exception,
       metrics.recon_metrics.row_comparison.missing_in_source AS missing_in_source,
       metrics.recon_metrics.row_comparison.missing_in_target AS missing_in_target,
       metrics.recon_metrics.column_comparison.absolute_mismatch AS absolute_mismatch,
       metrics.recon_metrics.column_comparison.threshold_mismatch AS threshold_mismatch,
       metrics.recon_metrics.column_comparison.mismatch_columns AS mismatch_columns,
       metrics.recon_metrics.schema_comparison AS schema_comparison,
       metrics.run_metrics.run_by_user AS executed_by,
       main.start_ts AS start_ts,
       main.end_ts AS end_ts
FROM remorph.reconcile.main main
         INNER JOIN remorph.reconcile.metrics metrics
                    ON main.recon_table_id = metrics.recon_table_id
ORDER BY metrics.inserted_ts DESC,
         main.recon_id,
         main.target_table.table_name
