-- Cloudera Manager: one row per host that CM manages. Hosts are not filtered by cluster, as in the original.
SELECT
    h->>'$.hostId' AS host_id,
    h->>'$.hostname' AS hostname,
    h->>'$.ipAddress' AS ip_address,
    h->>'$.rackId' AS rack_id,
    h->>'$.commissionState' AS commission_state,
    CAST(h->>'$.maintenanceMode' AS BOOLEAN) AS maintenance_mode,
    CAST(h->>'$.numCores' AS BIGINT) AS num_cores,
    CAST(h->>'$.numPhysicalCores' AS BIGINT) AS num_physical_cores,
    CAST(h->>'$.totalPhysMemBytes' AS BIGINT) AS total_memory_bytes,
    h->>'$.clusterRef.clusterName' AS cluster_name,
    h->>'$.distribution.name' AS os_name,
    h->>'$.distribution.version' AS os_version,
    CAST(h->'$.tags' AS VARCHAR) AS tags
FROM (SELECT unnest(hadoop_records(doc, '$.items')) AS h FROM raw_documents) AS t
