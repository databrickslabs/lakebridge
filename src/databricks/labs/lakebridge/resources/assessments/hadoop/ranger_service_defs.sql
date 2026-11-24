-- Ranger: one row per service definition (plugin type). accessTypes and resources are kept as JSON name lists.
SELECT
    CAST(d->>'$.id' AS BIGINT) AS id,
    d->>'$.name' AS name,
    d->>'$.label' AS label,
    d->>'$.displayName' AS display_name,
    d->>'$.implClass' AS impl_class,
    CAST(d->>'$.isEnabled' AS BOOLEAN) AS is_enabled,
    CAST(d->>'$.version' AS BIGINT) AS version,
    CAST(json_array_length(d, '$.accessTypes') AS BIGINT) AS access_type_count,
    CAST(d->'$.accessTypes[*].name' AS VARCHAR) AS access_types,
    CAST(d->'$.resources[*].name' AS VARCHAR) AS resource_names,
    CAST(d->'$.options' AS VARCHAR) AS options,
    epoch_ms(CAST(d->>'$.createTime' AS BIGINT)) AS create_time,
    epoch_ms(CAST(d->>'$.updateTime' AS BIGINT)) AS update_time
FROM raw_documents, UNNEST(hadoop_records(doc, '$')) AS t(d)
