/**
 * Multi-database variant: retrieves view metadata across every accessible, online user database by
 * dynamically UNION-ing INFORMATION_SCHEMA.VIEWS from each database, tagging each row with its source
 * `database_name`. VIEW_DEFINITION is redacted, matching the single-database extract. The WHERE 1 = 0
 * anchor guarantees a single, correctly-typed result set. Requires three-part naming (on-prem / MI).
 */
SET NOCOUNT ON;

DECLARE @cols NVARCHAR(MAX) =
    N'table_catalog, table_schema, table_name, check_option, is_updatable, ''[REDACTED]'' AS view_definition';
DECLARE @sql NVARCHAR(MAX) =
    N'SELECT DB_NAME() AS database_name, ' + @cols + N' FROM INFORMATION_SCHEMA.VIEWS WHERE 1 = 0';

SELECT @sql = @sql + ISNULL((
        SELECT ' UNION ALL SELECT ' + QUOTENAME([name], '''') + ' AS database_name, ' + @cols
               + ' FROM ' + QUOTENAME([name]) + '.INFORMATION_SCHEMA.VIEWS'
        FROM   sys.databases
        WHERE  state_desc = 'ONLINE'
               AND HAS_DBACCESS([name]) = 1
               AND [name] NOT IN ('master', 'tempdb', 'model', 'msdb')
        FOR XML PATH(''), TYPE).value('.', 'NVARCHAR(MAX)'), '');

EXEC sys.sp_executesql @sql;
