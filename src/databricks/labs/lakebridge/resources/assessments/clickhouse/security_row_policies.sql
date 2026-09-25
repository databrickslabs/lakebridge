-- Row policies (system.row_policies). Replicated: identical on OSS and Cloud.
-- select_filter redacted (default-on): the WHERE filter may reference PII columns / literal values.
-- apply_to_list / apply_to_except are Array(String), JSON-encoded.
SELECT
    name,
    short_name,
    database,
    table,
    id,
    storage,
    '[REDACTED]' AS select_filter,
    is_restrictive,
    apply_to_all,
    toJSONString(apply_to_list) AS apply_to_list,
    toJSONString(apply_to_except) AS apply_to_except
FROM system.row_policies
