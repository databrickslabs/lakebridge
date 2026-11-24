-- Cloudera Manager: one row per cluster it manages. is_selected marks the cluster the profile is for.
SELECT
    c->>'$.name' AS cluster_name,
    c->>'$.displayName' AS display_name,
    c->>'$.fullVersion' AS full_version,
    doc->>'$.api_version' AS api_version,
    doc->>'$.cm_version.version' AS cm_version,
    COALESCE((c->>'$.name') = (doc->>'$.selected_cluster'), FALSE) AS is_selected
FROM raw_documents, UNNEST(hadoop_records(doc, '$.clusters.items')) AS t(c)
