/**
 * Retrieves metadata for all tables in the specified database by querying
 * INFORMATION_SCHEMA.TABLES. Returns table definitions along with a timestamp
 * indicating when the data was extracted.
 */
SELECT TABLE_CATALOG,
       TABLE_SCHEMA,
       TABLE_NAME,
       TABLE_TYPE
FROM   INFORMATION_SCHEMA.TABLES;
