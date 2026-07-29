-- Replicated-table status (system.replicas).
SELECT
    database,
    table,
    is_leader,
    is_readonly,
    is_session_expired,
    absolute_delay,
    queue_size,
    inserts_in_queue,
    merges_in_queue,
    total_replicas,
    active_replicas,
    log_pointer,
    last_queue_update
FROM system.replicas
