/* --title 'Aggregates Reconciliation Details' --width 6 --height 6 */
WITH details_view AS (
    SELECT
        recon_table_id,
        rule_id,
        recon_type,
        explode(data) AS agg_details
    FROM
        remorph.reconcile.aggregate_details
),
     metrics_view AS (
         SELECT
             recon_table_id,
             rule_id,
             recon_metrics,
             run_metrics
         FROM
             remorph.reconcile.aggregate_metrics
     )
SELECT
    recon_id AS dd_recon_id,
    source_table AS dd_source_table,
    target_table AS dd_target_table,
    recon_type AS dd_recon_type,
    aggregate_type AS dd_aggregate_type,
    rule AS aggregate_column,
    source_value,
    target_value,
    zip_with(rule_group_by_columns, group_by_column_values, (groupby, value) -> CONCAT_WS(':', TRIM(groupby), value)) AS group_by_columns,
    COALESCE(status, 'false') AS status
FROM (
         SELECT
             main.recon_id,
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
             dtl.recon_type,
             rul.rule_info.agg_type AS aggregate_type,
             UPPER(rul.rule_info.agg_type) || CONCAT('(', rul.rule_info.agg_column, ')') AS rule,
             CONCAT_WS(
                     '_',
                     'source',
                     rul.rule_info.agg_type,
                     rul.rule_info.agg_column
             ) AS source_agg_column,
             dtl.agg_details[source_agg_column] AS source_value,
             CONCAT_WS(
                     '_',
                     'target',
                     rul.rule_info.agg_type,
                     rul.rule_info.agg_column
             ) AS target_agg_column,
             dtl.agg_details[target_agg_column] AS target_value,
             SPLIT(rul.rule_info.group_by_columns, ',') AS rule_group_by_columns,
             TRANSFORM(rule_group_by_columns, colm ->
                 COALESCE(dtl.agg_details[CONCAT('source_group_by_', TRIM(colm))],
                          dtl.agg_details[CONCAT('target_group_by_', TRIM(colm))])) AS group_by_column_values,
             CONCAT_WS(
                     '_',
                     'match',
                     rul.rule_info.agg_type,
                     rul.rule_info.agg_column
             ) AS status_column,
             dtl.agg_details[status_column] AS status
         FROM
             metrics_view mtc
                 INNER JOIN remorph.reconcile.main main ON main.recon_table_id = mtc.recon_table_id
                 INNER JOIN details_view dtl ON mtc.recon_table_id = dtl.recon_table_id
                 INNER JOIN remorph.reconcile.aggregate_rules rul ON mtc.rule_id = dtl.rule_id
                 AND dtl.rule_id = rul.rule_id
     )
ORDER BY
    recon_id
