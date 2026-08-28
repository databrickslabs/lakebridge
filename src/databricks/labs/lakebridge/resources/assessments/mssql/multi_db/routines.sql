/**
 * Multi-database variant: retrieves routine (stored procedure / function) metadata across every
 * accessible, online user database by dynamically UNION-ing INFORMATION_SCHEMA.ROUTINES from each
 * database, tagging each row with its source `database_name`. ROUTINE_DEFINITION is redacted, matching
 * the single-database extract. The WHERE 1 = 0 anchor guarantees a single, correctly-typed result set.
 * Requires three-part naming (on-prem SQL Server / Azure SQL Managed Instance only).
 */
SET NOCOUNT ON;

DECLARE @cols NVARCHAR(MAX) =
    N'created, data_type, is_deterministic, is_implicitly_invocable, is_null_call, is_user_defined_cast,'
    + N' last_altered, max_dynamic_result_sets, numeric_precision, numeric_precision_radix, numeric_scale,'
    + N' routine_body, routine_catalog, ''[REDACTED]'' AS routine_definition, routine_name, routine_schema,'
    + N' routine_type, schema_level_routine, specific_catalog, specific_name, specific_schema, sql_data_access';
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
