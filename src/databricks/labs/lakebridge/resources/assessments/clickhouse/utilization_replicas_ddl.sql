CREATE TABLE IF NOT EXISTS utilization_replicas (
    database VARCHAR,
    "table" VARCHAR,
    is_leader BIGINT,
    is_readonly BIGINT,
    is_session_expired BIGINT,
    absolute_delay BIGINT,
    queue_size BIGINT,
    inserts_in_queue BIGINT,
    merges_in_queue BIGINT,
    total_replicas BIGINT,
    active_replicas BIGINT,
    log_pointer BIGINT,
    last_queue_update TIMESTAMP
);
