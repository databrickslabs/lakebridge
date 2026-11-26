-- Ranger: one row per service; configs as returned (Ranger masks passwords as '*****').
SELECT
    CAST(s->>'$.id' AS BIGINT) AS id,
    s->>'$.name' AS name,
    s->>'$.type' AS service_type,
    s->>'$.displayName' AS display_name,
    s->>'$.description' AS description,
    CAST(s->>'$.isEnabled' AS BOOLEAN) AS is_enabled,
    s->>'$.tagService' AS tag_service,
    CAST(s->>'$.version' AS BIGINT) AS version,
    CAST(s->>'$.policyVersion' AS BIGINT) AS policy_version,
    CAST(s->>'$.tagVersion' AS BIGINT) AS tag_version,
    epoch_ms(CAST(s->>'$.createTime' AS BIGINT)) AS create_time,
    epoch_ms(CAST(s->>'$.updateTime' AS BIGINT)) AS update_time,
    s->>'$.createdBy' AS created_by,
    s->>'$.updatedBy' AS updated_by,
    CAST(s->'$.configs' AS VARCHAR) AS configs
FROM (SELECT unnest(hadoop_records(doc, '$')) AS s FROM raw_documents) AS t
