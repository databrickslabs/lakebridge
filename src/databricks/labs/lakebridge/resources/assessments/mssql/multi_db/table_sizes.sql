/**
 * Multi-database variant: retrieves storage and row-count statistics for user tables in every
 * accessible, online user database. sys.dm_db_partition_stats is a database-scoped DMV that resolves
 * against the current database context, so this cannot use three-part naming; instead it loops the
 * databases and runs the extract under USE <db> for each, accumulating rows tagged with their source
 * `database_name`. On-prem SQL Server / Azure SQL Managed Instance only.
 */
SET NOCOUNT ON;

DECLARE @results TABLE (
    database_name SYSNAME,
    TableName SYSNAME,
    [RowCount] BIGINT,
    ReservedMB BIGINT,
    UsedMB BIGINT,
    UnusedMB BIGINT,
    DataMB BIGINT,
    IndexMB BIGINT,
    extract_ts DATETIME2
);
DECLARE @name SYSNAME, @sql NVARCHAR(MAX);

DECLARE db_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT [name] FROM sys.databases
    WHERE  state_desc = 'ONLINE'
           AND HAS_DBACCESS([name]) = 1
           AND [name] NOT IN ('master', 'tempdb', 'model', 'msdb');
OPEN db_cursor;
FETCH NEXT FROM db_cursor INTO @name;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @sql = N'USE ' + QUOTENAME(@name) + N';'
        + N' SELECT DB_NAME() AS database_name, o.[name] AS TableName,'
        + N' SUM(ps.row_count) AS [RowCount],'
        + N' SUM(ps.reserved_page_count) * 8 / 1024 AS ReservedMB,'
        + N' SUM(ps.used_page_count) * 8 / 1024 AS UsedMB,'
        + N' (SUM(ps.reserved_page_count) - SUM(ps.used_page_count)) * 8 / 1024 AS UnusedMB,'
        + N' SUM(CASE WHEN ps.index_id < 2 THEN ps.in_row_data_page_count + ps.lob_used_page_count'
        + N' + ps.row_overflow_used_page_count ELSE 0 END) * 8 / 1024 AS DataMB,'
        + N' SUM(CASE WHEN ps.index_id >= 2 THEN ps.in_row_data_page_count ELSE 0 END) * 8 / 1024 AS IndexMB,'
        + N' SYSDATETIME() AS extract_ts'
        + N' FROM sys.dm_db_partition_stats AS ps'
        + N' JOIN sys.objects AS o ON ps.object_id = o.object_id'
        + N' WHERE o.type = ''U'''
        + N' GROUP BY SCHEMA_NAME(o.schema_id), o.[name]';
    INSERT INTO @results
    EXEC sys.sp_executesql @sql;
    FETCH NEXT FROM db_cursor INTO @name;
END
CLOSE db_cursor;
DEALLOCATE db_cursor;

SELECT database_name, TableName, [RowCount], ReservedMB, UsedMB, UnusedMB, DataMB, IndexMB, extract_ts
FROM   @results;
