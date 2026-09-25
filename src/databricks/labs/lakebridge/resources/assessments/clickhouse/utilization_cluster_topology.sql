-- Cluster topology (system.clusters). host_names is Array(String), JSON-encoded.
SELECT
    cluster,
    count(DISTINCT shard_num) AS shards,
    count(DISTINCT replica_num) AS replicas_per_shard,
    count(*) AS total_nodes,
    toJSONString(groupArray(DISTINCT host_name)) AS host_names
FROM system.clusters
WHERE cluster NOT IN ('system_metrics_log_cluster')
GROUP BY cluster
