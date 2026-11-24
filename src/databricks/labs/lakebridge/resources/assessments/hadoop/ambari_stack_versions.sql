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
    CAST(doc->'$.repository_versions' AS VARCHAR) AS repository_versions
FROM raw_documents, UNNEST(hadoop_records(doc, '$.ClusterStackVersions')) AS t(s)
