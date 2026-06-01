/**
 * Retrieves execution statistics for stored procedures from `sys.dm_exec_procedure_stats`,
 * including execution counts, total CPU and elapsed time, last execution timestamp,
 * and maps object and database IDs to their names. Results are ordered by most recent execution.
 */
SELECT database_id,
       Db_name(database_id)                AS db_name,
       object_id,
       Object_name(object_id, database_id) AS object_name,
       type,
       last_execution_time,
       execution_count,
       total_worker_time,
       total_elapsed_time,
       Sysdatetime()                       AS extract_ts
FROM   sys.dm_exec_procedure_stats
ORDER  BY last_execution_time
