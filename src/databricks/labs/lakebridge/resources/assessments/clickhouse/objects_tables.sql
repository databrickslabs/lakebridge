-- Table inventory with full metadata (system.tables). Replicated metadata: identical on OSS and Cloud.
-- create_table_query and as_select are redacted (default-on): DDL / view text may embed literal
-- predicate values, business logic, or PII. Array columns are JSON-encoded into a single VARCHAR
-- column so they land in one typed DuckDB column rather than exploding the schema.
SELECT
    database,
    name,
    engine,
    partition_key,
    sorting_key,
    primary_key,
    sampling_key,
    total_rows,
    total_bytes,
    lifetime_rows,
    lifetime_bytes,
    has_own_data,
    toJSONString(dependencies_database) AS dependencies_database,
    toJSONString(dependencies_table) AS dependencies_table,
    '[REDACTED]' AS create_table_query,
    engine_full,
    '[REDACTED]' AS as_select,
    comment
FROM system.tables
WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
ORDER BY database, name
