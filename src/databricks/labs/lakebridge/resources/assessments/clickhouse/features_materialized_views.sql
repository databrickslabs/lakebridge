-- Materialized views (system.tables). Replicated: identical on OSS and Cloud.
-- as_select / create_table_query redacted (default-on). dependencies_* are Array(String), JSON-encoded.
SELECT
    database,
    name,
    engine,
    '[REDACTED]' AS as_select,
    toJSONString(dependencies_database) AS dependencies_database,
    toJSONString(dependencies_table) AS dependencies_table,
    '[REDACTED]' AS create_table_query
FROM system.tables
WHERE engine = 'MaterializedView'
  AND database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
ORDER BY database, name
