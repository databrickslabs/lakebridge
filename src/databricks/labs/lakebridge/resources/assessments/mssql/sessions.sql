/**
 * Retrieves active user session details from `sys.dm_exec_sessions`, including login info,
 * program and client names, CPU and memory usage, request timing, row counts, and database context.
 * Excludes system sessions and orders results by the end time of the last request.
 */
SELECT   session_id,
         login_time,
         program_name,
         client_interface_name,
         CONVERT(VARCHAR(64), Hashbytes('SHA2_256', login_name), 1) AS login_name,
         status,
         cpu_time,
         memory_usage,
         total_scheduled_time,
         total_elapsed_time,
         last_request_start_time,
         last_request_end_time,
         is_user_process,
         row_count,
         database_id,
         Db_name(database_id) AS db_name,
         Sysdatetime()        AS extract_ts
FROM     sys.dm_exec_sessions
WHERE    is_user_process <> 0 {predicate}
ORDER BY last_request_end_time
