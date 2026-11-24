-- One row per NodeManager. Utilisation fields are only reported by some distributions; the rest are NULL.
SELECT
    n->>'$.id' AS node_id,
    n->>'$.rack' AS rack,
    n->>'$.state' AS state,
    n->>'$.version' AS version,
    CAST(n->>'$.numContainers' AS BIGINT) AS num_containers,
    CAST(n->>'$.usedMemoryMB' AS BIGINT) AS used_memory_mb,
    CAST(n->>'$.availMemoryMB' AS BIGINT) AS available_memory_mb,
    CAST(n->>'$.usedVirtualCores' AS BIGINT) AS used_virtual_cores,
    CAST(n->>'$.availableVirtualCores' AS BIGINT) AS available_virtual_cores,
    CAST(n->>'$.memUtilization' AS DOUBLE) AS mem_utilization,
    CAST(n->>'$.cpuUtilization' AS DOUBLE) AS cpu_utilization,
    epoch_ms(CAST(n->>'$.lastHealthUpdate' AS BIGINT)) AS last_health_update,
    n->>'$.nodeHostName' AS node_host_name,
    n->>'$.nodeHTTPAddress' AS node_http_address,
    NULLIF(n->>'$.healthReport', '') AS health_report,
    CAST(n->>'$.numQueuedContainers' AS BIGINT) AS num_queued_containers,
    CAST(n->'$.nodeLabels' AS VARCHAR) AS node_labels,
    -- What the NodeManager measures on the host itself, as opposed to what YARN allocated.
    CAST(n->>'$.resourceUtilization.nodePhysicalMemoryMB' AS BIGINT) AS node_physical_memory_mb,
    CAST(n->>'$.resourceUtilization.nodeVirtualMemoryMB' AS BIGINT) AS node_virtual_memory_mb,
    CAST(n->>'$.resourceUtilization.nodeCPUUsage' AS DOUBLE) AS node_cpu_usage,
    CAST(n->>'$.resourceUtilization.aggregatedContainersPhysicalMemoryMB' AS BIGINT) AS containers_physical_memory_mb,
    CAST(n->>'$.resourceUtilization.aggregatedContainersVirtualMemoryMB' AS BIGINT) AS containers_virtual_memory_mb,
    CAST(n->>'$.resourceUtilization.containersCPUUsage' AS DOUBLE) AS containers_cpu_usage
FROM (SELECT unnest(hadoop_records(doc, '$.nodes.node')) AS n FROM raw_documents) AS t
