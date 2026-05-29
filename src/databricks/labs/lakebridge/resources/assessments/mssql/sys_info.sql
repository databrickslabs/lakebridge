/**
 * Retrieves system-level information from SQL Server using sys.dm_os_sys_info.
 * Returns details about memory, CPU, scheduler count, and other OS-related
 * metadata for the SQL Server instance, along with a timestamp indicating when
 * the data was extracted.
 *
 * FIXME: trim both this query and sys_info_ddl.sql to the columns that matter
 * Many columns are internal scheduler and OS bookkeeping. On Azure SQL Database
 * (EngineEdition5) most hardware/memory columns return NULL anyway.
 */
SELECT cpu_ticks,
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
       softnuma_configuration,
       softnuma_configuration_desc,
       process_physical_affinity,
       sql_memory_model,
       sql_memory_model_desc,
       socket_count,
       cores_per_socket,
       numa_node_count,
       container_type,
       container_type_desc,
       Sysdatetime() AS extract_ts
FROM sys.dm_os_sys_info
