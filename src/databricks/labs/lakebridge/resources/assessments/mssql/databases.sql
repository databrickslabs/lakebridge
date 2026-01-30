/**
 * Retrieve metadata for all user databases, excluding system databases.
 * Returns each database's ID, name, collation, creation date,
 * and a timestamp indicating when the data was extracted.
 */
SELECT Db_id(NAME)   AS db_id,
       NAME,
       collation_name,
       create_date,
       Sysdatetime() AS extract_ts
FROM   sys.databases
WHERE  NAME NOT IN ( 'master', 'tempdb', 'model', 'msdb' );
