-- Cloudera Manager: one row per cluster it manages. is_selected marks the cluster the profile is for.
SELECT
    c->>'$.name' AS cluster_name,
    c->>'$.displayName' AS display_name,
    c->>'$.fullVersion' AS full_version,
    api_version,
    cm_version,
    COALESCE((c->>'$.name') = selected_cluster, FALSE) AS is_selected
FROM (
    SELECT
        doc->>'$.api_version' AS api_version,
        doc->>'$.cm_version.version' AS cm_version,
        doc->>'$.selected_cluster' AS selected_cluster,
        unnest(hadoop_records(doc, '$.clusters.items')) AS c
    FROM raw_documents
) AS t
