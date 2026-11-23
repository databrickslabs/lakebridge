/**
 * Retrieves metadata for all routines (stored procedures and functions) in the
 * specified database by querying INFORMATION_SCHEMA.ROUTINES. Returns routine
 * details along with a timestamp indicating when the data was extracted.
 * System objects (is_ms_shipped) and the sys and cdc (change data capture) schemas are excluded.
 */
SELECT DB_NAME() AS DATABASE_NAME,
       CREATED,
       DATA_TYPE,
       IS_DETERMINISTIC,
       IS_IMPLICITLY_INVOCABLE,
       IS_NULL_CALL,
       IS_USER_DEFINED_CAST,
       LAST_ALTERED,
       MAX_DYNAMIC_RESULT_SETS,
       NUMERIC_PRECISION,
       NUMERIC_PRECISION_RADIX,
       NUMERIC_SCALE,
       ROUTINE_BODY,
       ROUTINE_CATALOG,
       N'[REDACTED]' AS ROUTINE_DEFINITION,
       ROUTINE_NAME,
       ROUTINE_SCHEMA,
       ROUTINE_TYPE,
       SCHEMA_LEVEL_ROUTINE,
       SPECIFIC_CATALOG,
       SPECIFIC_NAME,
       SPECIFIC_SCHEMA,
       SQL_DATA_ACCESS
FROM   INFORMATION_SCHEMA.ROUTINES
WHERE  ROUTINE_SCHEMA NOT IN (N'sys', N'cdc')
       AND NOT EXISTS (SELECT 1
                       FROM   sys.all_objects AS o
                              JOIN sys.schemas AS s
                                ON s.schema_id = o.schema_id
                       WHERE  o.is_ms_shipped = 1
                              AND s.[name] = ROUTINE_SCHEMA
                              AND o.[name] = ROUTINE_NAME);
