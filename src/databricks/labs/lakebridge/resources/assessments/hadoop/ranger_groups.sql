-- Ranger groups (an admin-only API): identity and metadata only.
SELECT
    CAST(g->>'$.id' AS BIGINT) AS id,
    g->>'$.name' AS name,
    CAST(g->>'$.groupType' AS BIGINT) AS group_type,
    CAST(g->>'$.groupSource' AS BIGINT) AS group_source,
    CAST(g->>'$.isVisible' AS BIGINT) AS is_visible,
    CAST(g->>'$.createDate' AS TIMESTAMP) AS create_time,
    CAST(g->>'$.updateDate' AS TIMESTAMP) AS update_time
FROM raw_documents, UNNEST(hadoop_records(doc, '$')) AS t(g)
