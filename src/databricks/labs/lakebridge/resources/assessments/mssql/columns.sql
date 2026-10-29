/**
 * Retrieves column-level metadata for all tables and views in the specified
 * database by querying INFORMATION_SCHEMA.COLUMNS. Returns column attributes
 * along with a timestamp indicating when the data was extracted.
 * System objects (is_ms_shipped) and the sys and cdc (change data capture) schemas are excluded.
 */
SELECT DB_NAME() AS DATABASE_NAME,
       TABLE_CATALOG,
       TABLE_SCHEMA,
       TABLE_NAME,
       COLUMN_NAME,
       ORDINAL_POSITION,
       COLUMN_DEFAULT,
       IS_NULLABLE,
       DATA_TYPE,
       CHARACTER_MAXIMUM_LENGTH,
       CHARACTER_OCTET_LENGTH,
       NUMERIC_PRECISION,
       NUMERIC_PRECISION_RADIX,
       NUMERIC_SCALE,
       DATETIME_PRECISION,
       CHARACTER_SET_CATALOG,
       CHARACTER_SET_SCHEMA,
       CHARACTER_SET_NAME,
       COLLATION_CATALOG,
       COLLATION_SCHEMA,
       COLLATION_NAME,
       DOMAIN_CATALOG,
       DOMAIN_SCHEMA,
       DOMAIN_NAME
FROM   INFORMATION_SCHEMA.COLUMNS
WHERE  TABLE_SCHEMA NOT IN (N'sys', N'cdc')
       AND NOT EXISTS (SELECT 1
                       FROM   sys.all_objects AS o
                              JOIN sys.schemas AS s
                                ON s.schema_id = o.schema_id
                       WHERE  o.is_ms_shipped = 1
                              AND s.[name] = TABLE_SCHEMA
                              AND o.[name] = TABLE_NAME);
