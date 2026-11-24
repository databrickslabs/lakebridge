-- Ambari stack version of the cluster. host_states and repository_versions are kept as JSON text, as Ambari returns them.
SELECT
    s->>'$.cluster_name' AS cluster_name,
    CAST(s->>'$.id' AS BIGINT) AS id,
    s->>'$.stack' AS stack,
    s->>'$.version' AS version,
    CAST(s->>'$.repository_version' AS BIGINT) AS repository_version,
    s->>'$.state' AS state,
    CAST(s->>'$.supports_revert' AS BOOLEAN) AS supports_revert,
    CAST(s->'$.host_states' AS VARCHAR) AS host_states,
    repository_versions,
    CAST(s->'$.repository_summary' AS VARCHAR) AS repository_summary
FROM (
    SELECT CAST(doc->'$.repository_versions' AS VARCHAR) AS repository_versions,
        unnest(hadoop_records(doc, '$.ClusterStackVersions')) AS s
    FROM raw_documents
) AS t
