-- Static object dependency graph (system.tables). Replicated: identical on OSS and Cloud.
-- dependencies_* are Array(String), JSON-encoded to single columns.
SELECT
    database,
    name,
    engine,
    toJSONString(dependencies_database) AS dependencies_database,
    toJSONString(dependencies_table) AS dependencies_table
FROM system.tables
WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
  AND length(dependencies_table) > 0
ORDER BY database, name
