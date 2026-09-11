-- Active/recent mutations (system.mutations).
-- command / latest_fail_reason redacted (default-on): raw ALTER/DELETE DDL and failure text may
-- embed literal predicates / PII. parts_to_do_names is Array(String), JSON-encoded.
SELECT
    database,
    table,
    mutation_id,
    '[REDACTED]' AS command,
    create_time,
    is_done,
    toJSONString(parts_to_do_names) AS parts_to_do_names,
    parts_to_do,
    '[REDACTED]' AS latest_fail_reason
FROM system.mutations
WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
ORDER BY create_time DESC
LIMIT 100
