SELECT
    CONVERT(VARCHAR(64), HASHBYTES('SHA2_256', qs.sql_handle), 1) as sql_handle,
    qs.creation_time,
    qs.last_execution_time,
    qs.execution_count,
    qs.total_worker_time,
    qs.total_elapsed_time,
    qs.total_rows
FROM SYS.DM_EXEC_QUERY_STATS as qs
