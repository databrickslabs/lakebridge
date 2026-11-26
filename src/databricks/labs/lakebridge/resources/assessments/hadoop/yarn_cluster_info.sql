-- One row: the ResourceManager's identity and HA state. $distribution is the configured hadoop.distribution.
SELECT
    $distribution AS distribution,
    CAST(doc->>'$.clusterInfo.id' AS BIGINT) AS cluster_id,
    doc->>'$.clusterInfo.haState' AS ha_state,
    doc->>'$.clusterInfo.state' AS cluster_state,
    doc->>'$.clusterInfo.hadoopVersion' AS hadoop_version,
    doc->>'$.clusterInfo.resourceManagerVersion' AS resource_manager_version,
    epoch_ms(CAST(doc->>'$.clusterInfo.startedOn' AS BIGINT)) AS started_on
FROM raw_documents
