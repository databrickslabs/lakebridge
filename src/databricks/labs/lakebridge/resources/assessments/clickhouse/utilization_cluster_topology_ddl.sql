CREATE TABLE IF NOT EXISTS utilization_cluster_topology (
    cluster VARCHAR,
    shards BIGINT,
    replicas_per_shard BIGINT,
    total_nodes BIGINT,
    host_names VARCHAR
);
