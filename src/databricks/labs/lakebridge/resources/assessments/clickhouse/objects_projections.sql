-- Projections (system.projections). Replicated metadata: identical on OSS and Cloud.
-- sorting_key is an Array(String); JSON-encoded into a single VARCHAR column.
SELECT
    database,
    table,
    name AS projection_name,
    type,
    toJSONString(sorting_key) AS sorting_key
FROM system.projections
WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
ORDER BY database, table
