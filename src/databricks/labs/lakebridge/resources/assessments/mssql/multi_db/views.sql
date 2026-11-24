/**
 * Multi-database variant: retrieves view metadata across every accessible, online user database by
 * dynamically UNION-ing INFORMATION_SCHEMA.VIEWS from each database, tagging each row with its source
 * `database_name`. VIEW_DEFINITION is redacted, matching the single-database extract. The WHERE 1 = 0
 * anchor guarantees a single, correctly-typed result set. Requires three-part naming (on-prem / MI).
 * System objects (is_ms_shipped) and the sys and cdc (change data capture) schemas are excluded.
 */
SET NOCOUNT ON;

DECLARE @cols NVARCHAR(MAX) =
    N'TABLE_CATALOG COLLATE DATABASE_DEFAULT AS TABLE_CATALOG,'
    + N' TABLE_SCHEMA COLLATE DATABASE_DEFAULT AS TABLE_SCHEMA,'
    + N' TABLE_NAME COLLATE DATABASE_DEFAULT AS TABLE_NAME,'
    + N' CHECK_OPTION COLLATE DATABASE_DEFAULT AS CHECK_OPTION,'
    + N' IS_UPDATABLE COLLATE DATABASE_DEFAULT AS IS_UPDATABLE, N''[REDACTED]'' COLLATE DATABASE_DEFAULT AS VIEW_DEFINITION';
DECLARE @sql NVARCHAR(MAX) =
    N'SELECT DB_NAME() AS DATABASE_NAME, ' + @cols + N' FROM INFORMATION_SCHEMA.VIEWS WHERE 1 = 0';

SELECT @sql = @sql + ISNULL((
        SELECT ' UNION ALL SELECT N' + QUOTENAME([name], '''') + ' AS DATABASE_NAME, ' + @cols
               + ' FROM ' + QUOTENAME([name]) + '.INFORMATION_SCHEMA.VIEWS'
               + ' WHERE TABLE_SCHEMA NOT IN (N''sys'', N''cdc'') AND NOT EXISTS (SELECT 1 FROM ' + QUOTENAME([name]) + '.sys.all_objects AS o'
               + ' JOIN ' + QUOTENAME([name]) + '.sys.schemas AS s ON s.schema_id = o.schema_id'
               + ' WHERE o.is_ms_shipped = 1 AND s.[name] = TABLE_SCHEMA AND o.[name] = TABLE_NAME)'
        FROM   sys.databases
        WHERE  state_desc = 'ONLINE'
               AND HAS_DBACCESS([name]) = 1
               AND [name] NOT IN ('master', 'tempdb', 'model', 'msdb')
        FOR XML PATH(''), TYPE).value('.', 'NVARCHAR(MAX)'), '');

EXEC sys.sp_executesql @sql;
