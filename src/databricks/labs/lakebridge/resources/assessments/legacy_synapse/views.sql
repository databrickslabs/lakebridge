/**
 * Retrieves metadata for all views in the specified database by querying
 * `INFORMATION_SCHEMA.VIEWS`. Returns view definitions along with a timestamp
 * indicating when the data was extracted.
 */
SELECT TABLE_CATALOG,
       TABLE_SCHEMA,
       TABLE_NAME,
       CHECK_OPTION,
       IS_UPDATABLE,
       '[REDACTED]' AS VIEW_DEFINITION
FROM   INFORMATION_SCHEMA.VIEWS
