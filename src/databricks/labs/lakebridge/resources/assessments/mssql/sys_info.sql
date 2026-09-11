/**
 * Retrieves system-level information from SQL Server using sys.dm_os_sys_info.
 * Returns details about memory, CPU, scheduler count, and other OS-related
 * metadata for the SQL Server instance, along with a timestamp indicating when
 * the data was extracted.
 *
 * Several columns of sys.dm_os_sys_info were introduced after SQL Server 2012
 * (e.g. process_physical_affinity in 2017). Selecting them directly fails at
 * bind time on older engines with "Invalid column name" (error 207), which
 * aborts the whole profiler run. Rather than gate on a version NUMBER, this
 * query probes each column's existence with COL_LENGTH and substitutes a typed
 * NULL when it is absent. That is robust across BOTH old on-prem versions and
 * editions that omit columns (e.g. Azure SQL Database). The result set always
 * projects the full column list, so sys_info_ddl.sql needs no change and no
 * data is lost on modern servers.
 *
 * Dynamic SQL mirrors the existing pattern in multi_db/columns.sql.
 */
SET NOCOUNT ON;

-- For a version-gated column: emit the real column if it exists on this server,
-- otherwise a typed NULL under the same alias, preserving result-set shape/order.
DECLARE @softnuma_configuration      NVARCHAR(200) = CASE WHEN COL_LENGTH('sys.dm_os_sys_info','softnuma_configuration')      IS NOT NULL THEN 'softnuma_configuration'      ELSE 'CAST(NULL AS INT)'            END + ' AS softnuma_configuration';
DECLARE @softnuma_configuration_desc NVARCHAR(200) = CASE WHEN COL_LENGTH('sys.dm_os_sys_info','softnuma_configuration_desc') IS NOT NULL THEN 'softnuma_configuration_desc' ELSE 'CAST(NULL AS NVARCHAR(60))'   END + ' AS softnuma_configuration_desc';
DECLARE @process_physical_affinity   NVARCHAR(200) = CASE WHEN COL_LENGTH('sys.dm_os_sys_info','process_physical_affinity')   IS NOT NULL THEN 'process_physical_affinity'   ELSE 'CAST(NULL AS NVARCHAR(3072))' END + ' AS process_physical_affinity';
DECLARE @sql_memory_model            NVARCHAR(200) = CASE WHEN COL_LENGTH('sys.dm_os_sys_info','sql_memory_model')            IS NOT NULL THEN 'sql_memory_model'            ELSE 'CAST(NULL AS INT)'            END + ' AS sql_memory_model';
DECLARE @sql_memory_model_desc       NVARCHAR(200) = CASE WHEN COL_LENGTH('sys.dm_os_sys_info','sql_memory_model_desc')       IS NOT NULL THEN 'sql_memory_model_desc'       ELSE 'CAST(NULL AS NVARCHAR(60))'   END + ' AS sql_memory_model_desc';
DECLARE @socket_count                NVARCHAR(200) = CASE WHEN COL_LENGTH('sys.dm_os_sys_info','socket_count')                IS NOT NULL THEN 'socket_count'                ELSE 'CAST(NULL AS INT)'            END + ' AS socket_count';
DECLARE @cores_per_socket            NVARCHAR(200) = CASE WHEN COL_LENGTH('sys.dm_os_sys_info','cores_per_socket')            IS NOT NULL THEN 'cores_per_socket'            ELSE 'CAST(NULL AS INT)'            END + ' AS cores_per_socket';
DECLARE @numa_node_count             NVARCHAR(200) = CASE WHEN COL_LENGTH('sys.dm_os_sys_info','numa_node_count')             IS NOT NULL THEN 'numa_node_count'             ELSE 'CAST(NULL AS INT)'            END + ' AS numa_node_count';
DECLARE @container_type              NVARCHAR(200) = CASE WHEN COL_LENGTH('sys.dm_os_sys_info','container_type')              IS NOT NULL THEN 'container_type'              ELSE 'CAST(NULL AS INT)'            END + ' AS container_type';
DECLARE @container_type_desc         NVARCHAR(200) = CASE WHEN COL_LENGTH('sys.dm_os_sys_info','container_type_desc')         IS NOT NULL THEN 'container_type_desc'         ELSE 'CAST(NULL AS NVARCHAR(60))'   END + ' AS container_type_desc';

DECLARE @sql NVARCHAR(MAX) =
    N'SELECT cpu_ticks,
       ms_ticks,
       cpu_count,
       hyperthread_ratio,
       physical_memory_kb,
       virtual_memory_kb,
       committed_kb,
       committed_target_kb,
       visible_target_kb,
       stack_size_in_bytes,
       os_quantum,
       os_error_mode,
       os_priority_class,
       max_workers_count,
       scheduler_count,
       scheduler_total_count,
       deadlock_monitor_serial_number,
       sqlserver_start_time_ms_ticks,
       sqlserver_start_time,
       affinity_type,
       affinity_type_desc,
       process_kernel_time_ms,
       process_user_time_ms,
       time_source,
       time_source_desc,
       virtual_machine_type,
       virtual_machine_type_desc,
       ' + @softnuma_configuration + N',
       ' + @softnuma_configuration_desc + N',
       ' + @process_physical_affinity + N',
       ' + @sql_memory_model + N',
       ' + @sql_memory_model_desc + N',
       ' + @socket_count + N',
       ' + @cores_per_socket + N',
       ' + @numa_node_count + N',
       ' + @container_type + N',
       ' + @container_type_desc + N',
       SYSDATETIME() AS extract_ts
FROM sys.dm_os_sys_info';

EXEC sys.sp_executesql @sql;
