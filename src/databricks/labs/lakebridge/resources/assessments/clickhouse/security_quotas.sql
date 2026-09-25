-- Quotas (system.quotas). Replicated: identical on OSS and Cloud.
-- keys / durations / apply_to_* are Array columns, JSON-encoded to single columns.
SELECT
    name,
    id,
    storage,
    toJSONString(keys) AS keys,
    toJSONString(durations) AS durations,
    apply_to_all,
    toJSONString(apply_to_list) AS apply_to_list,
    toJSONString(apply_to_except) AS apply_to_except
FROM system.quotas
