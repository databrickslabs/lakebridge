/**
 * Multi-database variant: retrieves indexed views (clustered index, index_id = 1) across every
 * accessible, online user database by dynamically UNION-ing the sys.views / sys.schemas / sys.indexes
 * join from each database, tagging each row with its source `database_name`. The trailing 1 = 0 anchor
 * guarantees a single, correctly-typed result set. Requires three-part naming (on-prem / MI only).
 */
SET NOCOUNT ON;

DECLARE @sql NVARCHAR(MAX) =
    N'SELECT DB_NAME() AS database_name, v.[name] AS indexed_view_name, s.[name] AS schema_name,'
    + N' i.[name] AS index_name, i.[type_desc] AS index_type, i.[index_id], SYSDATETIME() AS extract_ts'
    + N' FROM sys.views AS v'
    + N' JOIN sys.schemas AS s ON v.[schema_id] = s.[schema_id]'
    + N' JOIN sys.indexes AS i ON v.[object_id] = i.[object_id]'
    + N' WHERE i.[index_id] = 1 AND 1 = 0';

SELECT @sql = @sql + ISNULL((
        SELECT ' UNION ALL SELECT ' + QUOTENAME([name], '''') + ' AS database_name,'
               + ' v.[name], s.[name], i.[name], i.[type_desc], i.[index_id], SYSDATETIME()'
               + ' FROM ' + QUOTENAME([name]) + '.sys.views AS v'
               + ' JOIN ' + QUOTENAME([name]) + '.sys.schemas AS s ON v.[schema_id] = s.[schema_id]'
               + ' JOIN ' + QUOTENAME([name]) + '.sys.indexes AS i ON v.[object_id] = i.[object_id]'
               + ' WHERE i.[index_id] = 1'
        FROM   sys.databases
        WHERE  state_desc = 'ONLINE'
               AND HAS_DBACCESS([name]) = 1
               AND [name] NOT IN ('master', 'tempdb', 'model', 'msdb')
        FOR XML PATH(''), TYPE).value('.', 'NVARCHAR(MAX)'), '');

EXEC sys.sp_executesql @sql;
