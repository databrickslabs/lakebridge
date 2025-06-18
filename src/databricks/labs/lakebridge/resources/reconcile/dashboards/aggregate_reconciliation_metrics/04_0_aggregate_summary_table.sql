/* --title 'Aggregates Summary Table' --width 6 --height 6 */
SELECT
    main.recon_id,
    main.source_type,
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
    CONCAT_WS(
            '.',
            target_catalog,
            target_schema,
            target_table_name
    ) AS target_table,
    UPPER(rules.rule_info.agg_type) || CONCAT('(', rules.rule_info.agg_column, ')') AS aggregate_column,
    rules.rule_info.group_by_columns,
    metrics.run_metrics.status AS status,
    metrics.run_metrics.exception_message AS exception,
    metrics.recon_metrics.missing_in_source AS missing_in_source,
    metrics.recon_metrics.missing_in_target AS missing_in_target,
    metrics.recon_metrics.mismatch AS mismatch,
    metrics.run_metrics.run_by_user AS executed_by,
    main.start_ts AS start_ts,
    main.end_ts AS end_ts
FROM
    remorph.reconcile.main main
        INNER JOIN remorph.reconcile.aggregate_metrics metrics
        INNER JOIN remorph.reconcile.aggregate_rules rules
                   ON main.recon_table_id = metrics.recon_table_id
                       AND rules.rule_id = metrics.rule_id
ORDER BY
    metrics.inserted_ts DESC,
    main.recon_id,
    main.target_table.table_name
