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
    epoch_ms(CAST(n->>'$.lastHealthUpdate' AS BIGINT)) AS last_health_update
FROM raw_documents, UNNEST(hadoop_records(doc, '$.nodes.node')) AS t(n)
