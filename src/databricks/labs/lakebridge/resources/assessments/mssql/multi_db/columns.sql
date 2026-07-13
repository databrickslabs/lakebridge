/**
 * Multi-database variant: retrieves column-level metadata across every accessible, online user
 * database by dynamically UNION-ing INFORMATION_SCHEMA.COLUMNS from each database, tagging each row
 * with its source `database_name`. The WHERE 1 = 0 anchor guarantees a single, correctly-typed result
 * set. Requires three-part naming (on-prem SQL Server / Azure SQL Managed Instance only).
 */
SET NOCOUNT ON;

DECLARE @cols NVARCHAR(MAX) =
    N'table_catalog, table_schema, table_name, column_name, ordinal_position, column_default,'
    + N' is_nullable, data_type, character_maximum_length, character_octet_length, numeric_precision,'
    + N' numeric_precision_radix, numeric_scale, datetime_precision, character_set_catalog,'
    + N' character_set_schema, character_set_name, collation_catalog, collation_schema, collation_name,'
    + N' domain_catalog, domain_schema, domain_name';
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
