/**
 * Retrieves metadata for all routines (stored procedures and functions) in the
 * specified database by querying INFORMATION_SCHEMA.ROUTINES. Returns routine
 * details along with a timestamp indicating when the data was extracted.
 */
SELECT CREATED,
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
       '[REDACTED]' AS ROUTINE_DEFINITION,
       ROUTINE_NAME,
       ROUTINE_SCHEMA,
       ROUTINE_TYPE,
       SCHEMA_LEVEL_ROUTINE,
       SPECIFIC_CATALOG,
       SPECIFIC_NAME,
       SPECIFIC_SCHEMA,
       SQL_DATA_ACCESS
FROM   INFORMATION_SCHEMA.ROUTINES;
