CREATE TABLE cm_hosts (
    host_id VARCHAR,
    hostname VARCHAR,
    ip_address VARCHAR,
    rack_id VARCHAR,
    commission_state VARCHAR,
    maintenance_mode BOOLEAN,
    num_cores BIGINT,
    num_physical_cores BIGINT,
    total_memory_bytes BIGINT
)
