-- Impala /backends: one row per backend the daemon knows. Sizes in bytes; process_start_time as Impala prints it.
SELECT
    coordinator AS coordinator_url,
    b->>'$.address' AS address,
    b->>'$.backend_id' AS backend_id,
    b->>'$.webserver_url' AS webserver_url,
    TRY_CAST(b->>'$.is_coordinator' AS BOOLEAN) AS is_coordinator,
    TRY_CAST(b->>'$.is_executor' AS BOOLEAN) AS is_executor,
    TRY_CAST(b->>'$.is_active' AS BOOLEAN) AS is_active,
    TRY_CAST(b->>'$.is_quiescing' AS BOOLEAN) AS is_quiescing,
    TRY_CAST(b->>'$.is_blacklisted' AS BOOLEAN) AS is_blacklisted,
    NULLIF(b->>'$.executor_groups', '') AS executor_groups,
    impala_bytes(b->>'$.admit_mem_limit') AS admit_mem_limit_bytes,
    impala_bytes(b->>'$.mem_reserved') AS mem_reserved_bytes,
    impala_bytes(b->>'$.mem_admitted') AS mem_admitted_bytes,
    TRY_CAST(b->>'$.admission_slots' AS BIGINT) AS admission_slots,
    TRY_CAST(b->>'$.admission_slots_in_use' AS BIGINT) AS admission_slots_in_use,
    TRY_CAST(b->>'$.num_admitted' AS BIGINT) AS num_admitted,
    impala_timestamp(b->>'$.process_start_time') AS process_start_time,
    b->>'$.version' AS version
FROM (
    SELECT doc->>'$.coordinator' AS coordinator, unnest(hadoop_records(doc, '$.body.backends')) AS b FROM raw_documents
) AS t
