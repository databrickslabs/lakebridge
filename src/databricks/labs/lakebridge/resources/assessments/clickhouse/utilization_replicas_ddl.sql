CREATE TABLE IF NOT EXISTS utilization_replicas (
    database VARCHAR,
    "table" VARCHAR,
    is_leader BIGINT,
    is_readonly BIGINT,
    is_session_expired BIGINT,
    absolute_delay UBIGINT,
    queue_size UBIGINT,
    inserts_in_queue UBIGINT,
    merges_in_queue UBIGINT,
    total_replicas UBIGINT,
    active_replicas UBIGINT,
    log_pointer UBIGINT,
    last_queue_update TIMESTAMP
);
