/* --title 'Schema Details' --width 6 */
WITH tmp AS (
    SELECT
        recon_table_id,
        inserted_ts,
        explode(data) AS schema_data
    FROM
        remorph.reconcile.details
    WHERE
        recon_type = 'schema'
)
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
    CONCAT(main.target_table.catalog, '.', main.target_table.schema, '.', main.target_table.table_name) AS target_table,
    schema_data['source_column'] AS source_column,
    schema_data['source_datatype'] AS source_datatype,
    schema_data['databricks_column'] AS databricks_column,
    schema_data['databricks_datatype'] AS databricks_datatype,
    schema_data['is_valid'] AS is_valid
FROM
    remorph.reconcile.main main
        INNER JOIN tmp ON main.recon_table_id = tmp.recon_table_id
ORDER BY
    tmp.inserted_ts DESC,
    main.recon_id,
    main.target_table
