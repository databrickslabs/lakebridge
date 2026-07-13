/**
 * Multi-database variant: retrieves data-file sizing for every accessible, online user database.
 * FILEPROPERTY() resolves against the current database context, so this cannot use three-part naming;
 * instead it loops the databases and runs the extract under USE <db> for each, accumulating rows tagged
 * with their source `database_name`. On-prem SQL Server / Azure SQL Managed Instance only (USE is
 * unsupported on Azure SQL Database, which uses the single-database variant).
 */
SET NOCOUNT ON;

DECLARE @results TABLE (
    database_name SYSNAME,
    FileName SYSNAME,
    type_desc NVARCHAR(60),
    CurrentSizeMB FLOAT,
    FreeSpaceInMB FLOAT,
    MaxSize BIGINT,
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
        + N' SELECT DB_NAME() AS database_name, [name] AS FileName, type_desc,'
        + N' CAST(size / 128.0 AS FLOAT) AS CurrentSizeMB,'
        + N' CAST(size / 128.0 - CAST(FILEPROPERTY([name], ''SpaceUsed'') AS INT) / 128.0 AS FLOAT) AS FreeSpaceInMB,'
        + N' CAST(max_size AS BIGINT) AS MaxSize, SYSDATETIME() AS extract_ts'
        + N' FROM sys.database_files WHERE type = 0';
    INSERT INTO @results
    EXEC sys.sp_executesql @sql;
    FETCH NEXT FROM db_cursor INTO @name;
END
CLOSE db_cursor;
DEALLOCATE db_cursor;

SELECT database_name, FileName, type_desc, CurrentSizeMB, FreeSpaceInMB, MaxSize, extract_ts
FROM   @results;
