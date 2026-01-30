/**
 * Retrieves metadata for all tables in the specified database by querying
 * INFORMATION_SCHEMA.TABLES. Returns table definitions along with a timestamp
 * indicating when the data was extracted.
 */
SELECT table_catalog,
       table_schema,
       table_name,
       table_type
FROM   information_schema.tables;
