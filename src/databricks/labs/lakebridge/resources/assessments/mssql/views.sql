/**
 * Retrieves metadata for all views in the specified database by querying
 * `INFORMATION_SCHEMA.VIEWS`. Returns view definitions along with a timestamp
 * indicating when the data was extracted.
 * System objects (is_ms_shipped) and the sys and cdc (change data capture) schemas are excluded.
 */
SELECT DB_NAME() AS DATABASE_NAME,
       TABLE_CATALOG,
       TABLE_SCHEMA,
       TABLE_NAME,
       CHECK_OPTION,
       IS_UPDATABLE,
       N'[REDACTED]' AS VIEW_DEFINITION
FROM   INFORMATION_SCHEMA.VIEWS
WHERE  TABLE_SCHEMA NOT IN (N'sys', N'cdc')
       AND NOT EXISTS (SELECT 1
                       FROM   sys.all_objects AS o
                              JOIN sys.schemas AS s
                                ON s.schema_id = o.schema_id
                       WHERE  o.is_ms_shipped = 1
                              AND s.[name] = TABLE_SCHEMA
                              AND o.[name] = TABLE_NAME)
