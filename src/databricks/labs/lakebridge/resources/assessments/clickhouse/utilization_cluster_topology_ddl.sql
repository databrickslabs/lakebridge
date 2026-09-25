CREATE TABLE IF NOT EXISTS utilization_cluster_topology (
    cluster VARCHAR,
    shards UBIGINT,
    replicas_per_shard UBIGINT,
    total_nodes UBIGINT,
    host_names VARCHAR
);
