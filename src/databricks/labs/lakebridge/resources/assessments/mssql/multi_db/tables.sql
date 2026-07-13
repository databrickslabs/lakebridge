/**
 * Multi-database variant: retrieves table metadata across every accessible, online user database on
 * the instance by dynamically UNION-ing INFORMATION_SCHEMA.TABLES from each database. Each row is
 * tagged with its source database via the projected `database_name` literal. The leading WHERE 1 = 0
 * anchor guarantees a single, correctly-typed result set even when no user database is accessible.
 * Requires three-part naming (on-prem SQL Server / Azure SQL Managed Instance only).
 */
SET NOCOUNT ON;

DECLARE @cols NVARCHAR(MAX) = N'table_catalog, table_schema, table_name, table_type';
DECLARE @sql NVARCHAR(MAX) =
    N'SELECT DB_NAME() AS database_name, ' + @cols + N' FROM INFORMATION_SCHEMA.TABLES WHERE 1 = 0';

SELECT @sql = @sql + ISNULL((
        SELECT ' UNION ALL SELECT ' + QUOTENAME([name], '''') + ' AS database_name, ' + @cols
               + ' FROM ' + QUOTENAME([name]) + '.INFORMATION_SCHEMA.TABLES'
        FROM   sys.databases
        WHERE  state_desc = 'ONLINE'
               AND HAS_DBACCESS([name]) = 1
               AND [name] NOT IN ('master', 'tempdb', 'model', 'msdb')
        FOR XML PATH(''), TYPE).value('.', 'NVARCHAR(MAX)'), '');

EXEC sys.sp_executesql @sql;
