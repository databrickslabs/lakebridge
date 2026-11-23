-- Regular views (system.tables). Replicated: identical on OSS and Cloud.
-- as_select / create_table_query redacted (default-on).
SELECT
    database,
    name,
    '[REDACTED]' AS as_select,
    '[REDACTED]' AS create_table_query
FROM system.tables
WHERE engine = 'View'
  AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
ORDER BY database, name
