SELECT
    request_id,
    session_id,
    status,
    submit_time,
    start_time,
    end_compile_time,
    end_time,
    total_elapsed_time,
    [label],
    error_id,
    database_id,
    command,
    resource_class,
    CURRENT_TIMESTAMP AS extract_ts
FROM sys.dm_pdw_exec_requests
WHERE start_time IS NOT NULL
  AND command IS NOT NULL
