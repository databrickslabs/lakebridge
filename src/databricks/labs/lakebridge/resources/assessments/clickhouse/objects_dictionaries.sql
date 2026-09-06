-- Dictionaries (system.dictionaries). Replicated metadata: identical on OSS and Cloud.
-- source is redacted (default-on): it carries the external-source connection string (host/port/user/db),
-- the same network detail the host_* fields promise to strip. The key/attribute name/type Array(String)
-- columns are JSON-encoded into single VARCHAR columns.
SELECT
    database,
    name,
    status,
    origin,
    type,
    toJSONString(key.names) AS key_names,
    toJSONString(key.types) AS key_types,
    toJSONString(attribute.names) AS attr_names,
    toJSONString(attribute.types) AS attr_types,
    element_count,
    bytes_allocated,
    loading_duration,
    last_successful_update_time,
    loading_start_time,
    '[REDACTED]' AS source
FROM system.dictionaries
