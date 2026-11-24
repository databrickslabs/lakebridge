CREATE TABLE yarn_nodes (
    node_id VARCHAR,
    rack VARCHAR,
    state VARCHAR,
    version VARCHAR,
    num_containers BIGINT,
    used_memory_mb BIGINT,
    available_memory_mb BIGINT,
    used_virtual_cores BIGINT,
    available_virtual_cores BIGINT,
    mem_utilization DOUBLE,
    cpu_utilization DOUBLE,
    last_health_update TIMESTAMP
)
