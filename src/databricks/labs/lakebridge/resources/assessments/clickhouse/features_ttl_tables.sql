-- Tables using TTL (system.tables). Replicated: identical on OSS and Cloud.
-- create_table_query redacted (default-on) but TTL presence is filtered on the source before redaction.
SELECT
    database,
    name,
    engine,
    '[REDACTED]' AS create_table_query
FROM system.tables
WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
  AND create_table_query LIKE '%TTL%'
ORDER BY database, name
