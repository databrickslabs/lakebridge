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
    CAST(h->>'$.totalPhysMemBytes' AS BIGINT) AS total_memory_bytes
FROM raw_documents, UNNEST(hadoop_records(doc, '$.items')) AS t(h)
