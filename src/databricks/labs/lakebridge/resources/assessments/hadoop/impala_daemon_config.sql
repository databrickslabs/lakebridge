-- Impala /varz: the flags for query-log retention, memory limits and admission control, one row per daemon and flag
-- (current and default value). A flag the daemon lacks has no row.
SELECT
    coordinator AS coordinator_url,
    f->>'$.name' AS flag_name,
    f->>'$.type' AS flag_type,
    f->>'$.current' AS flag_value,
    f->>'$.default' AS default_value
FROM (
    SELECT doc->>'$.coordinator' AS coordinator, unnest(hadoop_records(doc, '$.body.flags')) AS f FROM raw_documents
) AS t
WHERE f->>'$.name' IN (
    'query_log_size',
    'query_log_size_in_bytes',
    'query_log_write_interval_s',
    'enable_workload_mgmt',
    'mem_limit',
    'admission_control_slots',
    'max_admission_queue_size',
    'default_pool_max_requests',
    'default_pool_max_queued',
    'default_pool_mem_limit',
    'disable_pool_max_requests',
    'disable_pool_mem_limits',
    'default_query_options',
    'webserver_require_ldap',
    'webserver_require_spnego',
    'cluster_id'
)
