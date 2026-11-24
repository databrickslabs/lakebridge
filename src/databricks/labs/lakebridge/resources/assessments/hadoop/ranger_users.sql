-- Ranger users (an admin-only API). Only identity and metadata are selected: the password, e-mail address and names
-- are not read.
SELECT
    CAST(u->>'$.id' AS BIGINT) AS id,
    u->>'$.name' AS name,
    CAST(u->>'$.status' AS BIGINT) AS status,
    CAST(u->>'$.isVisible' AS BIGINT) AS is_visible,
    CAST(u->>'$.userSource' AS BIGINT) AS user_source,
    CAST(u->'$.groupNameList' AS VARCHAR) AS group_names,
    CAST(u->'$.userRoleList' AS VARCHAR) AS role_names,
    CAST(u->>'$.createDate' AS TIMESTAMP) AS create_time,
    CAST(u->>'$.updateDate' AS TIMESTAMP) AS update_time
FROM raw_documents, UNNEST(hadoop_records(doc, '$')) AS t(u)
