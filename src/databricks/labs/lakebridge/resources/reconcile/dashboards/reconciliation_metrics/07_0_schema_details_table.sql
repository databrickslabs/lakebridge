/* --title 'Schema Details' --width 6 */
SELECT
    main.recon_id,
    main.source_table.`catalog` AS source_catalog,
    main.source_table.`schema` AS source_schema,
    main.source_table.table_name AS source_table_name,
    IF(
        ISNULL(source_catalog),
        CONCAT_WS('.', source_schema, source_table_name),
        CONCAT_WS('.', source_catalog, source_schema, source_table_name)
    ) AS source_table,
    main.target_table.`catalog` AS target_catalog,
    main.target_table.`schema` AS target_schema,
    main.target_table.table_name AS target_table_name,
    CONCAT(main.target_table.catalog, '.', main.target_table.schema, '.', main.target_table.table_name) AS target_table,
    sd.source_column,
    sd.source_datatype,
    sd.databricks_column,
    sd.databricks_datatype,
    sd.is_valid
FROM
    remorph.reconcile.main main
        INNER JOIN remorph.reconcile.schema_details sd ON main.recon_table_id = sd.recon_table_id
ORDER BY
    sd.inserted_ts DESC,
    main.recon_id,
    main.target_table
