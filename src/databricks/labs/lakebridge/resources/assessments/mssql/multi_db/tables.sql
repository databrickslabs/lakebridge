/**
 * Multi-database variant: retrieves table metadata across every accessible, online user database on
 * the instance by dynamically UNION-ing INFORMATION_SCHEMA.TABLES from each database. Each row is
 * tagged with its source database via the projected `database_name` literal. The leading WHERE 1 = 0
 * anchor guarantees a single, correctly-typed result set even when no user database is accessible.
 * Requires three-part naming (on-prem SQL Server / Azure SQL Managed Instance only).
 * System objects (is_ms_shipped) and the sys and cdc (change data capture) schemas are excluded.
 */
SET NOCOUNT ON;

DECLARE @cols NVARCHAR(MAX) =
    N'TABLE_CATALOG COLLATE DATABASE_DEFAULT AS TABLE_CATALOG,'
    + N' TABLE_SCHEMA COLLATE DATABASE_DEFAULT AS TABLE_SCHEMA,'
    + N' TABLE_NAME COLLATE DATABASE_DEFAULT AS TABLE_NAME,'
    + N' TABLE_TYPE COLLATE DATABASE_DEFAULT AS TABLE_TYPE';
DECLARE @sql NVARCHAR(MAX) =
    N'SELECT DB_NAME() AS DATABASE_NAME, ' + @cols + N' FROM INFORMATION_SCHEMA.TABLES WHERE 1 = 0';

SELECT @sql = @sql + ISNULL((
        SELECT ' UNION ALL SELECT N' + QUOTENAME([name], '''') + ' AS DATABASE_NAME, ' + @cols
               + ' FROM ' + QUOTENAME([name]) + '.INFORMATION_SCHEMA.TABLES'
               + ' WHERE TABLE_SCHEMA NOT IN (N''sys'', N''cdc'') AND NOT EXISTS (SELECT 1 FROM ' + QUOTENAME([name]) + '.sys.all_objects AS o'
               + ' JOIN ' + QUOTENAME([name]) + '.sys.schemas AS s ON s.schema_id = o.schema_id'
               + ' WHERE o.is_ms_shipped = 1 AND s.[name] = TABLE_SCHEMA AND o.[name] = TABLE_NAME)'
        FROM   sys.databases
        WHERE  state_desc = 'ONLINE'
               AND HAS_DBACCESS([name]) = 1
               AND [name] NOT IN ('master', 'tempdb', 'model', 'msdb')
        FOR XML PATH(''), TYPE).value('.', 'NVARCHAR(MAX)'), '');

EXEC sys.sp_executesql @sql;
