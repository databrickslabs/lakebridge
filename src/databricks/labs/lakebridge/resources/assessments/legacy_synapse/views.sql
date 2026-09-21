/**
 * Retrieves metadata for all views in the specified database by querying
 * `INFORMATION_SCHEMA.VIEWS`. Returns view definitions along with a timestamp
 * indicating when the data was extracted.
 */
SELECT table_catalog,
       table_schema,
       table_name,
       check_option,
       is_updatable,
       '[REDACTED]' AS view_definition
FROM   information_schema.views;
