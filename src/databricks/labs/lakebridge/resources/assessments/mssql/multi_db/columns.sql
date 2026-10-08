/**
 * Multi-database variant: retrieves column-level metadata across every accessible, online user
 * database by dynamically UNION-ing INFORMATION_SCHEMA.COLUMNS from each database, tagging each row
 * with its source `database_name`. The WHERE 1 = 0 anchor guarantees a single, correctly-typed result
 * set. Requires three-part naming (on-prem SQL Server / Azure SQL Managed Instance only).
 */
SET NOCOUNT ON;

DECLARE @cols NVARCHAR(MAX) =
    N'TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, COLUMN_DEFAULT,'
    + N' IS_NULLABLE, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, CHARACTER_OCTET_LENGTH, NUMERIC_PRECISION,'
    + N' NUMERIC_PRECISION_RADIX, NUMERIC_SCALE, DATETIME_PRECISION, CHARACTER_SET_CATALOG,'
    + N' CHARACTER_SET_SCHEMA, CHARACTER_SET_NAME, COLLATION_CATALOG, COLLATION_SCHEMA, COLLATION_NAME,'
    + N' DOMAIN_CATALOG, DOMAIN_SCHEMA, DOMAIN_NAME';
DECLARE @sql NVARCHAR(MAX) =
    N'SELECT DB_NAME() AS database_name, ' + @cols + N' FROM INFORMATION_SCHEMA.COLUMNS WHERE 1 = 0';

SELECT @sql = @sql + ISNULL((
        SELECT ' UNION ALL SELECT ' + QUOTENAME([name], '''') + ' AS database_name, ' + @cols
               + ' FROM ' + QUOTENAME([name]) + '.INFORMATION_SCHEMA.COLUMNS'
        FROM   sys.databases
        WHERE  state_desc = 'ONLINE'
               AND HAS_DBACCESS([name]) = 1
               AND [name] NOT IN ('master', 'tempdb', 'model', 'msdb')
        FOR XML PATH(''), TYPE).value('.', 'NVARCHAR(MAX)'), '');

EXEC sys.sp_executesql @sql;
