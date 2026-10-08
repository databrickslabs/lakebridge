/**
 * Multi-database variant: retrieves routine (stored procedure / function) metadata across every
 * accessible, online user database by dynamically UNION-ing INFORMATION_SCHEMA.ROUTINES from each
 * database, tagging each row with its source `database_name`. ROUTINE_DEFINITION is redacted, matching
 * the single-database extract. The WHERE 1 = 0 anchor guarantees a single, correctly-typed result set.
 * Requires three-part naming (on-prem SQL Server / Azure SQL Managed Instance only).
 */
SET NOCOUNT ON;

DECLARE @cols NVARCHAR(MAX) =
    N'CREATED, DATA_TYPE, IS_DETERMINISTIC, IS_IMPLICITLY_INVOCABLE, IS_NULL_CALL, IS_USER_DEFINED_CAST,'
    + N' LAST_ALTERED, MAX_DYNAMIC_RESULT_SETS, NUMERIC_PRECISION, NUMERIC_PRECISION_RADIX, NUMERIC_SCALE,'
    + N' ROUTINE_BODY, ROUTINE_CATALOG, ''[REDACTED]'' AS ROUTINE_DEFINITION, ROUTINE_NAME, ROUTINE_SCHEMA,'
    + N' ROUTINE_TYPE, SCHEMA_LEVEL_ROUTINE, SPECIFIC_CATALOG, SPECIFIC_NAME, SPECIFIC_SCHEMA, SQL_DATA_ACCESS';
DECLARE @sql NVARCHAR(MAX) =
    N'SELECT DB_NAME() AS database_name, ' + @cols + N' FROM INFORMATION_SCHEMA.ROUTINES WHERE 1 = 0';

SELECT @sql = @sql + ISNULL((
        SELECT ' UNION ALL SELECT ' + QUOTENAME([name], '''') + ' AS database_name, ' + @cols
               + ' FROM ' + QUOTENAME([name]) + '.INFORMATION_SCHEMA.ROUTINES'
        FROM   sys.databases
        WHERE  state_desc = 'ONLINE'
               AND HAS_DBACCESS([name]) = 1
               AND [name] NOT IN ('master', 'tempdb', 'model', 'msdb')
        FOR XML PATH(''), TYPE).value('.', 'NVARCHAR(MAX)'), '');

EXEC sys.sp_executesql @sql;
